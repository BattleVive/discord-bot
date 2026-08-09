from __future__ import annotations

import asyncio
import math
import time

import discord
from discord import app_commands

from . import db
from .logs import logger


class CommandAccessService:
    def __init__(self) -> None:
        self._cooldowns: dict[tuple[int, int], float] = {}
        self._lock = asyncio.Lock()

    async def check(self, interaction: discord.Interaction, command: app_commands.Command) -> bool:
        if not command.extras.get("public"):
            return True
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return False
        channel_id = getattr(interaction.channel, "parent_id", None) or interaction.channel_id
        rules = await db.get_command_channel_rules(guild.id)
        live_rules: dict[int, str] = {}
        for rule in rules:
            stored_id = rule["channel_id"]
            if guild.get_channel(stored_id) is None:
                try:
                    await db.remove_command_channel_rule(guild.id, stored_id)
                except Exception:
                    logger.exception("Could not remove stale command-channel rule %s.", stored_id)
                continue
            live_rules[stored_id] = rule["rule"]
        allowed = live_rules.get(channel_id) != "block"
        if allowed and "allow" in live_rules.values():
            allowed = live_rules.get(channel_id) == "allow"
        if not allowed:
            await interaction.response.send_message(
                "This command is not available in this channel.", ephemeral=True
            )
            return False

        seconds = await db.get_rank_cooldown_seconds(guild.id)
        if command.extras.get("cooldown_setting") != "rank" or seconds == 0:
            return True
        now = time.monotonic()
        key = (guild.id, interaction.user.id)
        async with self._lock:
            self._cooldowns = {
                existing_key: timestamp
                for existing_key, timestamp in self._cooldowns.items()
                if now - timestamp < 3600
            }
            previous = self._cooldowns.get(key)
            if previous is not None and now - previous < seconds:
                retry_after = math.ceil(seconds - (now - previous))
                await interaction.response.send_message(
                    f"Please wait {retry_after} seconds before using this command again.",
                    ephemeral=True,
                )
                return False
            self._cooldowns[key] = now
        return True


class BattleviveCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command = interaction.command
        service = getattr(self.client, "command_access_service", None)
        if command is None or service is None:
            return True
        try:
            return await service.check(interaction, command)
        except Exception:
            logger.exception("Public-command access evaluation failed.")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "The command failed. Please try again later.",
                    ephemeral=True,
                )
            return False
