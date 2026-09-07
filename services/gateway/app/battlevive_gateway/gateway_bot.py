"""Discord-only gateway command registration."""
from __future__ import annotations

import base64
from io import BytesIO
import time

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

from .database import connect_and_verify
from .diagnostics import snapshot_attachment
from .diagnostics import upstream_snapshot
from .gateway import TEMPORARILY_UNAVAILABLE
from .integrations import ImageRendererClient
from .integrations import UpstreamDataClient
from .guides import sync_configured_guides
from .logs import logger
from .leaderboards import LeaderboardService
from .refresh import RefreshCoordinator
from .rank import rank_render_model
from .roles import GUIDE_UPDATES_ROLE
from .roles import create_required_roles
from .roles import reconcile_member_rank
from .repositories import RoleRepository
from .repositories import GuildConfigRepository
from .repositories import RuleRepository


def bypasses_channel_rules(command_name: str) -> bool:
    return command_name == "config" or command_name.startswith("config ") or command_name == "debug" or command_name.startswith("debug ")


class GatewayBot(commands.Bot):
    def __init__(self, *args: object, database_url: str | None = None,
                 command_guild_id: int | None = None, upstream_data_url: str | None = None,
                 image_renderer_url: str | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.database_url = database_url
        self.command_guild_id = command_guild_id
        self.pool: object | None = None
        self.upstream = UpstreamDataClient(upstream_data_url) if upstream_data_url else None
        self.renderer = ImageRendererClient(image_renderer_url) if image_renderer_url else None
        self.leaderboard_service: LeaderboardService | None = None
        self.rank_cooldowns: dict[tuple[int, int], float] = {}
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        if self.database_url is not None:
            self.pool = await connect_and_verify(self.database_url)
        await self._start_health_server()
        if self.pool is not None and self.upstream is not None and self.renderer is not None:
            self.leaderboard_service = LeaderboardService(self, self.pool, self.upstream, self.renderer)
            self.leaderboard_service.start()
        if self.command_guild_id is None:
            await self.tree.sync()
        else:
            guild = discord.Object(id=self.command_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        logger.info("gateway setup complete database_ready=%s development_guild=%s", self.pool is not None, self.command_guild_id is not None)

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        if self.pool is not None:
            if self.leaderboard_service is not None:
                await self.leaderboard_service.stop()
                self.leaderboard_service = None
            await self.pool.close()  # type: ignore[union-attr]
        await super().close()

    async def _start_health_server(self) -> None:
        app = web.Application()
        async def health(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})
        async def ready(_: web.Request) -> web.Response:
            is_ready = self.pool is not None
            return web.json_response({"status": "ready" if is_ready else "not_ready"}, status=200 if is_ready else 503)
        app.router.add_get("/health", health)
        app.router.add_get("/ready", ready)
        self._health_runner = web.AppRunner(app)
        await self._health_runner.setup()
        await web.TCPSite(self._health_runner, "0.0.0.0", 8080).start()

    async def command_allowed(self, guild_id: int, command_name: str, channel_id: int | None) -> bool:
        if channel_id is None or self.pool is None:
            return False
        async with self.pool.acquire() as connection:  # type: ignore[union-attr]
            return await RuleRepository(connection).allows(guild_id, command_name, channel_id)


def create_bot(*, database_url: str | None = None, command_guild_id: int | None = None,
               upstream_data_url: str | None = None, image_renderer_url: str | None = None) -> GatewayBot:
    intents = discord.Intents.default()
    intents.members = True
    bot = GatewayBot(command_prefix=(), intents=intents, database_url=database_url,
                     command_guild_id=command_guild_id, upstream_data_url=upstream_data_url,
                     image_renderer_url=image_renderer_url)

    config = app_commands.Group(name="config", description="Configure BattleVive for this server")
    config_commands = app_commands.Group(
        name="commands",
        description="Configure public-command channels",
        parent=config,
    )

    async def configuration(interaction: discord.Interaction) -> tuple[int, int] | None:
        if interaction.guild_id is None or interaction.user is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return None
        member = interaction.user
        if isinstance(member, discord.Member) and not member.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return None
        if bot.pool is None:
            await interaction.response.send_message("Configuration storage is temporarily unavailable.", ephemeral=True)
            return None
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            repository = GuildConfigRepository(connection)
            await repository.ensure(interaction.guild_id, member.id)
            current = await repository.get(interaction.guild_id)
        if current is None or not isinstance(current.get("version"), int):
            await interaction.response.send_message("Configuration storage is temporarily unavailable.", ephemeral=True)
            return None
        return interaction.guild_id, current["version"]

    @config.command(name="leaderboard-limit", description="Set leaderboard entry limit")
    async def leaderboard_limit(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100]) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"leaderboard_limit": limit}, updated_by=interaction.user.id)
        await interaction.response.send_message("Leaderboard limit updated.", ephemeral=True)

    @config.command(name="leaderboard-channel", description="Set the automatic leaderboard channel")
    async def leaderboard_channel(interaction: discord.Interaction, channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"leaderboard_channel_id": channel.id}, updated_by=interaction.user.id)
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        await interaction.response.send_message("Automatic leaderboard channel updated.", ephemeral=True)

    @config.command(name="guide-forum", description="Set the forum used for guide publication")
    async def guide_forum(interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"guide_forum_channel_id": channel.id}, updated_by=interaction.user.id)
        await interaction.response.send_message("Guide forum updated.", ephemeral=True)

    @config.command(name="channel-rule", description="Allow or block a command in a channel")
    async def channel_rule(interaction: discord.Interaction, command_name: str, channel: discord.abc.GuildChannel, allowed: bool) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).set(guild_id, command_name.casefold().strip(), channel.id, allowed)
        await interaction.response.send_message("Command channel rule updated.", ephemeral=True)

    @config.command(name="command-whitelist", description="Allow a command in a channel")
    async def command_whitelist(interaction: discord.Interaction, command_name: str,
                                channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).set(guild_id, command_name.casefold().strip(), channel.id, True)
        await interaction.response.send_message("Command channel whitelisted.", ephemeral=True)

    @config.command(name="command-blacklist", description="Block a command in a channel")
    async def command_blacklist(interaction: discord.Interaction, command_name: str,
                                channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).set(guild_id, command_name.casefold().strip(), channel.id, False)
        await interaction.response.send_message("Command channel blacklisted.", ephemeral=True)

    @config_commands.command(name="whitelist", description="Allow public commands in a channel")
    async def commands_whitelist(interaction: discord.Interaction,
                                 channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).set(guild_id, "*", channel.id, True)
        await interaction.response.send_message("Public commands are enabled in this channel.", ephemeral=True)

    @config_commands.command(name="blacklist", description="Block public commands in a channel")
    async def commands_blacklist(interaction: discord.Interaction,
                                 channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).set(guild_id, "*", channel.id, False)
        await interaction.response.send_message("Public commands are disabled in this channel.", ephemeral=True)

    @config_commands.command(name="remove", description="Remove a public-command channel rule")
    async def commands_remove(interaction: discord.Interaction,
                              channel: discord.abc.GuildChannel) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).remove(guild_id, "*", channel.id)
        await interaction.response.send_message("Public-command channel rule removed.", ephemeral=True)

    @config.command(name="rank-cooldown", description="Set the /rank cooldown in seconds")
    async def rank_cooldown(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"rank_cooldown_seconds": seconds}, updated_by=interaction.user.id)
        await interaction.response.send_message(
            "The /rank cooldown is disabled." if seconds == 0 else f"The /rank cooldown is now {seconds} seconds.",
            ephemeral=True,
        )

    @config.command(name="show", description="Show this server's bot configuration")
    async def config_show(interaction: discord.Interaction) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            configuration_row = await GuildConfigRepository(connection).get(guild_id)
            rules = await RuleRepository(connection).list(guild_id)
        if configuration_row is None:
            await interaction.response.send_message("Configuration storage is temporarily unavailable.", ephemeral=True)
            return
        leaderboard_channel = configuration_row.get("leaderboard_channel_id")
        active_lobby_channel = configuration_row.get("active_lobby_channel_id")
        active_lobby_role = configuration_row.get("active_lobby_role_id")
        moderator_role = configuration_row.get("website_moderator_role_id")
        guide_forum = configuration_row.get("guide_forum_channel_id")
        guide_role = configuration_row.get("guide_notification_role_id")
        lines = [
            f"Leaderboard channel: <#{leaderboard_channel}>." if leaderboard_channel else "Leaderboard channel: not configured.",
            f"Leaderboard limit: {configuration_row['leaderboard_limit']}.",
            f"Rank cooldown: {configuration_row['rank_cooldown_seconds']} seconds.",
            f"Active-lobby channel: <#{active_lobby_channel}>." if active_lobby_channel else "Active-lobby channel: not configured.",
            f"Active-lobby notification role: <@&{active_lobby_role}>." if active_lobby_role else "Active-lobby notification role: not configured.",
            f"Website moderator role: <@&{moderator_role}>." if moderator_role else "Website moderator role: not configured.",
            f"Guide forum: <#{guide_forum}>." if guide_forum else "Guide forum: not configured.",
            f"Guide notification role: <@&{guide_role}>." if guide_role else "Guide notification role: not configured.",
            "Guide automatic deletion: enabled." if configuration_row.get("guide_auto_delete_on_removal") else "Guide automatic deletion: disabled.",
            "Debug commands: enabled." if configuration_row.get("debug_commands_enabled") else "Debug commands: disabled.",
            "Command rules: " + (", ".join(
                f"{rule['command_name']} {'allow' if rule['allowed'] else 'block'} <#{rule['channel_id']}>" for rule in rules
            ) if rules else "none."),
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @config.command(name="debug", description="Enable or disable administrator diagnostics")
    async def debug_enabled(interaction: discord.Interaction, enabled: bool) -> None:
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(
                guild_id, version, {"debug_commands_enabled": enabled}, updated_by=interaction.user.id
            )
        await interaction.response.send_message(
            f"Administrator diagnostics {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    bot.tree.add_command(config)

    @bot.tree.command(name="create_roles", description="Create BattleVive rank roles")
    async def create_roles(interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Manage Roles permission is required.", ephemeral=True)
            return
        if bot.pool is None:
            await interaction.response.send_message("Configuration storage is temporarily unavailable.", ephemeral=True)
            return
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).ensure(interaction.guild.id, interaction.user.id)
            try:
                repository = GuildConfigRepository(connection)
                current = await repository.get(interaction.guild.id)
                created, existing = await create_required_roles(interaction.guild, RoleRepository(connection))
                guide_role = next((role for role in interaction.guild.roles if role.name == GUIDE_UPDATES_ROLE), None)
                if (guide_role is not None and current is not None
                        and current.get("guide_notification_role_id") is None):
                    await repository.update(
                        interaction.guild.id,
                        current["version"],
                        {"guide_notification_role_id": guide_role.id},
                        updated_by=interaction.user.id,
                    )
            except RuntimeError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
        summary = []
        if created:
            summary.append("Created: " + ", ".join(created))
        if existing:
            summary.append("Already configured: " + ", ".join(existing))
        await interaction.response.send_message("; ".join(summary) or "No roles changed.", ephemeral=True)

    debug = app_commands.Group(name="debug", description="Administrator diagnostics")

    @debug.command(name="upstream", description="Download validated upstream feature data")
    async def debug_upstream(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or bot.pool is None or bot.upstream is None:
            await interaction.response.send_message("Upstream diagnostics are temporarily unavailable.", ephemeral=True)
            return
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            configuration_row = await GuildConfigRepository(connection).get(interaction.guild_id)
        if not configuration_row or not configuration_row.get("debug_commands_enabled"):
            await interaction.response.send_message("Administrator diagnostics are disabled.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            attachment = snapshot_attachment(await upstream_snapshot(bot.upstream))
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        await interaction.followup.send(
            "Validated upstream diagnostics attached. This file is visible only to you.",
            file=discord.File(BytesIO(attachment), filename="upstream-debug.json"),
            ephemeral=True,
        )

    bot.tree.add_command(debug)

    async def channel_access(interaction: discord.Interaction) -> bool:
        command = interaction.command
        name = command.qualified_name if command is not None else ""
        if bypasses_channel_rules(name):
            return True
        if interaction.guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return False
        if await bot.command_allowed(interaction.guild_id, name, interaction.channel_id):
            return True
        await interaction.response.send_message("This command is not enabled in this channel.", ephemeral=True)
        return False

    bot.tree.interaction_check = channel_access

    @bot.tree.command(name="refresh", description="Refresh supported BattleVive integrations")
    async def refresh(interaction: discord.Interaction) -> None:
        # Guide reconciliation performs several upstream reads and Discord API
        # mutations. Acknowledge before it can exceed Discord's three-second
        # interaction response deadline.
        await interaction.response.defer(ephemeral=True, thinking=True)
        operations = {}
        if bot.upstream is not None:
            async def check_upstream() -> None:
                await bot.upstream.get_result("/guides", require_fresh=True)
            operations["upstream-data"] = check_upstream
            if bot.pool is not None and interaction.guild_id is not None:
                async def sync_guides() -> None:
                    await sync_configured_guides(bot, bot.pool, bot.upstream, interaction.guild_id)
                operations["guides"] = sync_guides
        if bot.renderer is not None:
            operations["image-renderer"] = bot.renderer.ready
        if bot.leaderboard_service is not None:
            operations["leaderboard"] = bot.leaderboard_service.reconcile_all
        report = await RefreshCoordinator(operations).run()
        logger.info("refresh completed=%s unavailable=%s failed=%s", report.completed, report.unavailable, report.failed)
        await interaction.followup.send(report.message(), ephemeral=True)

    @bot.tree.command(name="rank", description="Show your BattleVive rank")
    async def rank(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or bot.pool is None or bot.upstream is None or bot.renderer is None:
            await interaction.response.send_message(TEMPORARILY_UNAVAILABLE, ephemeral=True)
            return
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            config_row = await GuildConfigRepository(connection).get(interaction.guild_id)
        cooldown = config_row.get("rank_cooldown_seconds", 30) if config_row else 30
        key = (interaction.guild_id, interaction.user.id)
        now = time.monotonic()
        previous = bot.rank_cooldowns.get(key)
        if isinstance(cooldown, int) and cooldown > 0 and previous is not None and now - previous < cooldown:
            await interaction.response.send_message("Please wait before using /rank again.", ephemeral=True)
            return
        # The work can exceed Discord's interaction deadline.  Deferring
        # ephemerally keeps unavailable identity/upstream/render failures out
        # of the invoking channel.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from .repositories import IdentityRepository
            async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
                member_number = await IdentityRepository(connection).member_number_for_discord_id(interaction.user.id)
            if member_number is None:
                await interaction.followup.send("Your BattleVive identity has not been linked yet.", ephemeral=True)
                return
            profile = (await bot.upstream.get_result(f"/players/{member_number}", require_fresh=True)).data.get("player")
            if not isinstance(profile, dict):
                raise ValueError("upstream player profile was invalid")
            model = rank_render_model(profile)
            if isinstance(interaction.user, discord.Member):
                await reconcile_member_rank(interaction.user, profile)
            avatar = await interaction.user.display_avatar.with_size(128).read()
            model["avatar_png_base64"] = base64.b64encode(avatar).decode("ascii")
            png = await bot.renderer.render_rank(model)
        except Exception:
            logger.exception("rank rendering failed for Discord member %s", interaction.user.id)
            await interaction.followup.send("Your rank is temporarily unavailable.", ephemeral=True)
            return
        bot.rank_cooldowns[key] = now
        await interaction.followup.send(file=discord.File(BytesIO(png), filename="profile.png"))

    @bot.event
    async def on_member_join(member: discord.Member) -> None:
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        if bot.pool is None or bot.upstream is None:
            return
        try:
            from .repositories import IdentityRepository
            async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
                member_number = await IdentityRepository(connection).member_number_for_discord_id(member.id)
            if member_number is None:
                return
            profile = (await bot.upstream.get_result(f"/players/{member_number}", require_fresh=True)).data.get("player")
            if isinstance(profile, dict):
                await reconcile_member_rank(member, profile)
        except Exception:
            logger.exception("rank reconciliation failed for returning member %s", member.id)

    @bot.event
    async def on_member_remove(_member: discord.Member) -> None:
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
    return bot
