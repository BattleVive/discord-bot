"""Discord-only gateway command registration."""
from __future__ import annotations

import base64
import asyncio
from io import BytesIO
import time

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

from .active_lobbies import ActiveLobbyService
from .database import connect_and_verify
from .diagnostics import snapshot_attachment
from .diagnostics import upstream_snapshot
from .gateway import TEMPORARILY_UNAVAILABLE
from .integrations import ImageRendererClient
from .integrations import UpstreamDataClient
from .guides import GuideService
from .logs import logger
from .leaderboards import LeaderboardService
from .refresh import RefreshCoordinator
from .rank import rank_render_model
from .roles import GUIDE_UPDATES_ROLE
from .roles import create_required_roles
from .roles import reconcile_member_rank
from .repositories import ConcurrentUpdateError
from .repositories import GuildConfigRepository
from .repositories import PublicationRepository
from .repositories import RoleRepository
from .repositories import RuleRepository


def bypasses_channel_rules(command_name: str) -> bool:
    """Return whether a command bypasses publication-channel rules."""
    return command_name == "config" or command_name.startswith("config ") or command_name == "debug" or command_name.startswith("debug ")


def _has_channel_permissions(channel: object, member: object | None, *names: str) -> bool:
    """Return whether a member has every required channel permission."""
    permissions_for = getattr(channel, "permissions_for", None)
    if member is None or not callable(permissions_for):
        return False
    permissions = permissions_for(member)
    return all(bool(getattr(permissions, name, False)) for name in names)


def can_publish_leaderboard(guild: object | None, channel: object) -> bool:
    """Return whether the bot can publish a leaderboard in the channel."""
    return isinstance(channel, discord.TextChannel) and _has_channel_permissions(
        channel, getattr(guild, "me", None), "view_channel", "send_messages", "attach_files", "read_message_history"
    )


def can_publish_active_lobbies(guild: object | None, channel: object) -> bool:
    """Return whether the bot can publish active lobbies in the channel."""
    return isinstance(channel, discord.TextChannel) and _has_channel_permissions(
        channel, getattr(guild, "me", None), "view_channel", "send_messages", "embed_links", "read_message_history"
    )


def can_publish_guides(guild: object | None, channel: object) -> bool:
    """Return whether the bot can publish guides in the forum."""
    return isinstance(channel, discord.ForumChannel) and _has_channel_permissions(
        channel, getattr(guild, "me", None), "view_channel", "send_messages", "send_messages_in_threads",
        "read_message_history", "manage_threads",
    )


class GatewayBot(commands.Bot):
    """Coordinate the gateway Discord bot and its services."""
    def __init__(self, *args: object, database_url: str | None = None,
                 command_guild_id: int | None = None, upstream_data_url: str | None = None,
                 image_renderer_url: str | None = None, **kwargs: object) -> None:
        """Initialize the gateway bot instance."""
        super().__init__(*args, **kwargs)
        self.database_url = database_url
        self.command_guild_id = command_guild_id
        self.pool: object | None = None
        self.upstream = UpstreamDataClient(upstream_data_url) if upstream_data_url else None
        self.renderer = ImageRendererClient(image_renderer_url) if image_renderer_url else None
        self.leaderboard_service: LeaderboardService | None = None
        self.active_lobby_service: ActiveLobbyService | None = None
        self.guide_service: GuideService | None = None
        self.rank_cooldowns: dict[tuple[int, int], float] = {}
        self.refresh_cooldowns: dict[int, float] = {}
        self.refresh_lock = asyncio.Lock()
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        """Initialize private services and synchronize application commands."""
        if self.database_url is not None:
            self.pool = await connect_and_verify(self.database_url)
        await self._start_health_server()
        if self.pool is not None and self.upstream is not None and self.renderer is not None:
            self.leaderboard_service = LeaderboardService(self, self.pool, self.upstream, self.renderer)
            self.leaderboard_service.start()
        if self.pool is not None and self.upstream is not None:
            self.active_lobby_service = ActiveLobbyService(self, self.pool, self.upstream)
            self.active_lobby_service.start()
        if self.pool is not None and self.upstream is not None:
            self.guide_service = GuideService(self, self.pool, self.upstream)
            self.guide_service.start()
        if self.command_guild_id is None:
            await self.tree.sync()
        else:
            guild = discord.Object(id=self.command_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        logger.info("gateway setup complete database_ready=%s development_guild=%s", self.pool is not None, self.command_guild_id is not None)

    async def close(self) -> None:
        """Stop background services and close the bot cleanly."""
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        if self.pool is not None:
            if self.guide_service is not None:
                await self.guide_service.stop()
                self.guide_service = None
            if self.leaderboard_service is not None:
                await self.leaderboard_service.stop()
                self.leaderboard_service = None
            if self.active_lobby_service is not None:
                await self.active_lobby_service.stop()
                self.active_lobby_service = None
            await self.pool.close()  # type: ignore[union-attr]
        await super().close()

    async def _start_health_server(self) -> None:
        """Start the gateway health and readiness server."""
        app = web.Application()
        async def health(_: web.Request) -> web.Response:
            """Report gateway process health."""
            return web.json_response({"status": "ok"})
        async def ready(_: web.Request) -> web.Response:
            """Report whether database and Discord gateway are ready."""
            is_ready = self.deployment_ready()
            return web.json_response({"status": "ready" if is_ready else "not_ready"}, status=200 if is_ready else 503)
        app.router.add_get("/health", health)
        app.router.add_get("/ready", ready)
        self._health_runner = web.AppRunner(app)
        await self._health_runner.setup()
        await web.TCPSite(self._health_runner, "0.0.0.0", 8080).start()

    def deployment_ready(self) -> bool:
        """Return whether this slot can safely receive production traffic."""
        return self.pool is not None and self.is_ready()

    async def command_allowed(self, guild_id: int, command_name: str, channel_id: int | None) -> bool:
        """Return whether a command is allowed in the selected channel."""
        if channel_id is None or self.pool is None:
            return False
        async with self.pool.acquire() as connection:  # type: ignore[union-attr]
            return await RuleRepository(connection).allows(guild_id, command_name, channel_id)


def create_bot(*, database_url: str | None = None, command_guild_id: int | None = None,
               upstream_data_url: str | None = None, image_renderer_url: str | None = None) -> GatewayBot:
    """Create and configure the Discord gateway bot."""
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
    config_leaderboard = app_commands.Group(name="leaderboard", description="Configure leaderboard publication", parent=config)
    config_active_lobbies = app_commands.Group(name="active-lobbies", description="Configure active-lobby publication", parent=config)
    config_guides = app_commands.Group(name="guides", description="Configure guide publication", parent=config)
    config_rank = app_commands.Group(name="rank", description="Configure the public rank command", parent=config)
    config_reset = app_commands.Group(name="reset", description="Reset BattleVive publication settings", parent=config)

    async def configuration(interaction: discord.Interaction) -> tuple[int, int] | None:
        """Validate the interaction and load its guild configuration version."""
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
        """Set the guild's automatic leaderboard entry limit."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"leaderboard_limit": limit}, updated_by=interaction.user.id)
        await interaction.response.send_message("Leaderboard limit updated.", ephemeral=True)

    @config.command(name="leaderboard-channel", description="Set the automatic leaderboard channel")
    async def leaderboard_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        """Set the guild's automatic leaderboard channel."""
        context = await configuration(interaction)
        if context is None:
            return
        if not can_publish_leaderboard(interaction.guild, channel):
            await interaction.response.send_message(
                "Leaderboard channel requires View Channel, Send Messages, Attach Files, and Read Message History.",
                ephemeral=True,
            )
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"leaderboard_channel_id": channel.id}, updated_by=interaction.user.id)
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        await interaction.response.send_message("Automatic leaderboard channel updated.", ephemeral=True)

    @config.command(name="active-lobby-channel", description="Set the automatic active-lobby channel")
    async def active_lobby_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        """Set the guild's automatic active-lobby channel."""
        context = await configuration(interaction)
        if context is None:
            return
        if not can_publish_active_lobbies(interaction.guild, channel):
            await interaction.response.send_message(
                "Active-lobby channel requires View Channel, Send Messages, Embed Links, and Read Message History.",
                ephemeral=True,
            )
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"active_lobby_channel_id": channel.id}, updated_by=interaction.user.id)
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        await interaction.response.send_message("Automatic active-lobby channel updated.", ephemeral=True)

    @config.command(name="guide-forum", description="Set the forum used for guide publication")
    async def guide_forum(interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
        """Set the guild's guide publication forum."""
        context = await configuration(interaction)
        if context is None:
            return
        if not can_publish_guides(interaction.guild, channel):
            await interaction.response.send_message(
                "Guide forum requires View Channel, Send Messages, Send Messages in Threads, Read Message History, and Manage Threads.",
                ephemeral=True,
            )
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, {"guide_forum_channel_id": channel.id}, updated_by=interaction.user.id)
        if bot.guide_service is not None:
            bot.guide_service.request_reconciliation()
        await interaction.response.send_message("Guide forum updated.", ephemeral=True)

    @config_active_lobbies.command(name="role", description="Set the active-lobby notification role")
    async def active_lobby_role(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        """Set or clear the role mentioned when a newly seen lobby is published."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(
                guild_id, version, {"active_lobby_role_id": None if role is None else role.id}, updated_by=interaction.user.id
            )
        await interaction.response.send_message("Active-lobby notification role updated.", ephemeral=True)

    @config_active_lobbies.command(name="moderator-role", description="Set the disputed-match moderator role")
    async def active_lobby_moderator_role(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        """Set or clear the role used for disputed-match notifications."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(
                guild_id, version, {"website_moderator_role_id": None if role is None else role.id}, updated_by=interaction.user.id
            )
        await interaction.response.send_message("Disputed-match moderator role updated.", ephemeral=True)

    @config_guides.command(name="role", description="Set the guide notification role")
    async def guide_role(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        """Set or clear the role mentioned on new guide publication."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(
                guild_id, version, {"guide_notification_role_id": None if role is None else role.id}, updated_by=interaction.user.id
            )
        await interaction.response.send_message("Guide notification role updated.", ephemeral=True)

    @config_guides.command(name="automatic-deletion", description="Set removal of unpublished guide threads")
    async def guide_automatic_deletion(interaction: discord.Interaction, enabled: bool) -> None:
        """Set whether managed threads are deleted when their guide disappears."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(
                guild_id, version, {"guide_auto_delete_on_removal": enabled}, updated_by=interaction.user.id
            )
        await interaction.response.send_message(
            f"Guide automatic deletion {'enabled' if enabled else 'disabled'}.", ephemeral=True
        )

    @config.command(name="channel-rule", description="Allow or block a command in a channel")
    async def channel_rule(interaction: discord.Interaction, command_name: str, channel: discord.abc.GuildChannel, allowed: bool) -> None:
        """Set the guild's allow or deny rule for a command channel."""
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
        """Allow one command in a selected channel."""
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
        """Deny one command in a selected channel."""
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
        """Allow public commands in a selected channel."""
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
        """Deny public commands in a selected channel."""
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
        """Remove the guild's rule for a command channel."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, _ = context
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await RuleRepository(connection).remove(guild_id, "*", channel.id)
        await interaction.response.send_message("Public-command channel rule removed.", ephemeral=True)

    @config.command(name="rank-cooldown", description="Set the /rank cooldown in seconds")
    async def rank_cooldown(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        """Set the guild's rank-command cooldown."""
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

    # Keep the original flat commands during the transition while restoring the
    # grouped commands operators used in the 1.0 bot.
    @config_leaderboard.command(name="channel", description="Set the automatic leaderboard channel")
    async def grouped_leaderboard_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        """Delegate the grouped command to the canonical channel update."""
        await leaderboard_channel.callback(interaction, channel)

    @config_leaderboard.command(name="limit", description="Set leaderboard entry limit")
    async def grouped_leaderboard_limit(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100]) -> None:
        """Delegate the grouped command to the canonical limit update."""
        await leaderboard_limit.callback(interaction, limit)

    @config_active_lobbies.command(name="channel", description="Set the automatic active-lobby channel")
    async def grouped_active_lobby_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        """Delegate the grouped command to the canonical channel update."""
        await active_lobby_channel.callback(interaction, channel)

    @config_guides.command(name="channel", description="Set the forum used for guide publication")
    async def grouped_guide_forum(interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
        """Delegate the grouped command to the canonical forum update."""
        await guide_forum.callback(interaction, channel)

    @config_rank.command(name="cooldown", description="Set the /rank cooldown in seconds")
    async def grouped_rank_cooldown(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        """Delegate the grouped command to the canonical cooldown update."""
        await rank_cooldown.callback(interaction, seconds)

    async def reset_configuration(interaction: discord.Interaction, changes: dict[str, object], message: str,
                                  *, feature: str | None = None, archive_threads: bool = False) -> None:
        """Clear configuration and remove only that feature's managed Discord posts."""
        context = await configuration(interaction)
        if context is None:
            return
        guild_id, version = context
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).update(guild_id, version, changes, updated_by=interaction.user.id)

        async def cleanup() -> None:
            """Remove publications after their configuration is disabled."""
            if feature is None:
                return
            async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
                publications = await PublicationRepository(connection).list_for_feature(guild_id, feature)
            for publication in publications:
                try:
                    if archive_threads and isinstance(publication.get("thread_id"), int):
                        thread = await bot.fetch_channel(publication["thread_id"])
                        if isinstance(thread, discord.Thread):
                            await thread.edit(archived=True, locked=False, reason="BattleVive configuration reset")
                    elif isinstance(publication.get("channel_id"), int) and isinstance(publication.get("message_id"), int):
                        channel = interaction.guild.get_channel(publication["channel_id"]) if interaction.guild is not None else None
                        if isinstance(channel, discord.TextChannel):
                            await (await channel.fetch_message(publication["message_id"])).delete()
                except discord.NotFound:
                    pass
            async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
                await PublicationRepository(connection).delete_feature(guild_id, feature)

        if feature == "active-lobbies" and bot.active_lobby_service is not None:
            async with bot.active_lobby_service._reconcile_lock:
                await cleanup()
        else:
            await cleanup()
        await interaction.followup.send(message, ephemeral=True)

    @config_reset.command(name="leaderboard", description="Clear automatic leaderboard configuration")
    async def reset_leaderboard(interaction: discord.Interaction) -> None:
        """Disable future leaderboard publication without deleting unrelated settings."""
        await reset_configuration(interaction, {"leaderboard_channel_id": None}, "Leaderboard configuration reset.", feature="leaderboard")

    @config_reset.command(name="active-lobbies", description="Clear active-lobby configuration")
    async def reset_active_lobbies(interaction: discord.Interaction) -> None:
        """Disable future lobby publication and notification roles."""
        await reset_configuration(interaction, {
            "active_lobby_channel_id": None, "active_lobby_role_id": None,
            "website_moderator_role_id": None, "active_lobby_baseline_pending": True,
        }, "Active-lobby configuration reset.", feature="active-lobbies")

    @config_reset.command(name="guides", description="Clear guide-publication configuration")
    async def reset_guides(interaction: discord.Interaction) -> None:
        """Disable future guide publication and clear guide-specific preferences."""
        await reset_configuration(interaction, {
            "guide_forum_channel_id": None, "guide_notification_role_id": None,
            "guide_auto_delete_on_removal": False,
        }, "Guide configuration reset; managed guide posts were archived.", feature="guide", archive_threads=True)

    @config.command(name="show", description="Show this server's bot configuration")
    async def config_show(interaction: discord.Interaction) -> None:
        """Show the guild's current gateway configuration."""
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
        """Enable or disable guild diagnostic commands."""
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
        """Create and track the guild's required managed roles."""
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("Manage Roles permission is required.", ephemeral=True)
            return
        if bot.pool is None:
            await interaction.response.send_message("Configuration storage is temporarily unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            await GuildConfigRepository(connection).ensure(interaction.guild.id, interaction.user.id)
        try:
            created, existing, blocked = await create_required_roles(interaction.guild)
        except RuntimeError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("role setup failed for guild %s", interaction.guild.id)
            await interaction.followup.send(
                "Role setup failed. Check the bot role position and permissions.", ephemeral=True
            )
            return
        async with bot.pool.acquire() as connection:  # type: ignore[union-attr]
            ownership = RoleRepository(connection)
            for role in created:
                purpose = "guide_updates" if role.name == GUIDE_UPDATES_ROLE else "rank"
                await ownership.claim(interaction.guild.id, purpose, role.name.casefold(), role.id)
            guide_role = next((role for role in interaction.guild.roles if role.name == GUIDE_UPDATES_ROLE), None)
            current = await GuildConfigRepository(connection).get(interaction.guild.id)
            if (guide_role is not None and current is not None
                    and current.get("guide_notification_role_id") is None):
                try:
                    await GuildConfigRepository(connection).update(
                        interaction.guild.id,
                        current["version"],
                        {"guide_notification_role_id": guide_role.id},
                        updated_by=interaction.user.id,
                    )
                except ConcurrentUpdateError:
                    logger.warning("guide role link skipped for guild %s", interaction.guild.id)
        summary = []
        if created:
            summary.append("Created: " + ", ".join(role.name for role in created))
        if existing:
            summary.append("Already configured: " + ", ".join(existing))
        if blocked:
            summary.append("Skipped unsafe existing roles: " + ", ".join(blocked))
        await interaction.followup.send("; ".join(summary) or "No roles changed.", ephemeral=True)

    debug = app_commands.Group(name="debug", description="Administrator diagnostics")

    @debug.command(name="upstream", description="Download validated upstream feature data")
    async def debug_upstream(interaction: discord.Interaction) -> None:
        """Return a credential-safe snapshot of upstream service state."""
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
        """Report whether the current command is allowed in this channel."""
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
        """Refresh the guild's supported upstream-backed features."""
        guild_id = interaction.guild_id
        member = interaction.user
        if guild_id is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not bool(getattr(getattr(member, "guild_permissions", None), "manage_guild", False)):
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        now = time.monotonic()
        previous = bot.refresh_cooldowns.get(guild_id)
        if previous is not None and now - previous < 10:
            await interaction.response.send_message(
                "Please wait before refreshing this server again.", ephemeral=True
            )
            return
        if bot.refresh_lock.locked():
            await interaction.response.send_message("A refresh is already in progress.", ephemeral=True)
            return
        await bot.refresh_lock.acquire()
        bot.refresh_cooldowns[guild_id] = now
        # Guide reconciliation performs several upstream reads and Discord API
        # mutations. Acknowledge before it can exceed Discord's three-second
        # interaction response deadline.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            operations = {}
            if bot.upstream is not None:
                async def check_upstream() -> None:
                    """Check whether the private upstream service is ready."""
                    await bot.upstream.get_result("/guides", require_fresh=True)
                operations["upstream-data"] = check_upstream
                if bot.guide_service is not None:
                    async def sync_guides() -> None:
                        """Synchronize guides."""
                        await bot.guide_service.reconcile_guild(guild_id)
                    operations["guides"] = sync_guides
            if bot.renderer is not None:
                operations["image-renderer"] = bot.renderer.ready
            if bot.leaderboard_service is not None:
                operations["leaderboard"] = bot.leaderboard_service.reconcile_all
            if bot.active_lobby_service is not None:
                operations["active-lobbies"] = bot.active_lobby_service.reconcile_all
            report = await RefreshCoordinator(operations).run()
            logger.info("refresh completed=%s unavailable=%s failed=%s", report.completed, report.unavailable, report.failed)
            await interaction.followup.send(report.message(), ephemeral=True)
        finally:
            bot.refresh_lock.release()

    @bot.tree.command(name="rank", description="Show your BattleVive rank")
    async def rank(interaction: discord.Interaction) -> None:
        """Render the invoking member's current rank card."""
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
        # The work can exceed Discord's interaction deadline. Deferring keeps
        # unavailable upstream/render failures out of the invoking channel.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            profile = (await bot.upstream.get_result(
                f"/players/by-discord/{interaction.user.id}", require_fresh=True
            )).data.get("player")
            if not isinstance(profile, dict):
                await interaction.followup.send("No BattleVive profile is linked to your Discord account.", ephemeral=True)
                return
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
        """Reconcile rank roles for a joining member from the exact API profile."""
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        if bot.pool is None or bot.upstream is None:
            return
        try:
            profile = (await bot.upstream.get_result(
                f"/players/by-discord/{member.id}", require_fresh=True
            )).data.get("player")
            if isinstance(profile, dict):
                await reconcile_member_rank(member, profile)
        except Exception:
            logger.exception("rank reconciliation failed for returning member %s", member.id)

    @bot.event
    async def on_member_remove(_member: discord.Member) -> None:
        """Request leaderboard reconciliation after a member leaves."""
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
    return bot
