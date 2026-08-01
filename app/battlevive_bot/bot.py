from __future__ import annotations

import asyncio
from io import BytesIO
import json

# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord import app_commands
# pyrefly: ignore [missing-import]
from discord.ext import commands
# pyrefly: ignore [missing-import]
from discord.ext import tasks
# pyrefly: ignore [missing-import]
from PIL import Image

from . import db
from .active_lobbies import ActiveLobbyService
from .battlevive import BattleviveClient
from .db import close_pool
from .db import get_pool
from .db import init_pool
from .db import MissingUsersError
from .db import sync_battlevive_data_to_db
from .images import build_card
from .leaderboards import LeaderboardService
from .logs import logger
from .models import Lobby
from .models import SeasonRating
from .models import User
from .roles import ACTIVE_LOBBY_ROLE
from .roles import create_roles
from .roles import give_battlevive_role
from .roles import give_rank_roles
from .roles import reconcile_member_roles
from .roles import WEBSITE_MODERATOR_ROLE
from .settings import ASSETS_DIR
from .settings import BATTLEVIVE_BOOTSTRAP_JWT
from .settings import BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN
from .settings import BATTLEVIVE_TOKEN_PATH
from .settings import BATTLEVIVE_URL
from .settings import DATABASE_URL
from .settings import DATA_DIR
from .settings import DISCORD_COMMAND_GUILD_ID
from .settings import DISCORD_BOT_TOKEN
from .settings import LEADERBOARD_MAX_ENTRIES
from .settings import SUPABASE_API_KEY
from .settings import SUPABASE_URL
from .settings import validate_runtime_settings


battlevive_client = BattleviveClient(
    bootstrap_access_token=BATTLEVIVE_BOOTSTRAP_JWT,
    bootstrap_refresh_token=BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN,
    token_path=BATTLEVIVE_TOKEN_PATH,
    supabase_url=SUPABASE_URL,
    supabase_api_key=SUPABASE_API_KEY,
)

battlevive_users: list[User] = []
lobbies: list[Lobby] = []
season_ratings: list[SeasonRating] = []
_manual_refresh_lock = asyncio.Lock()
_debug_export_running = False

SAFE_COMMAND_ERROR = "The command failed. Please try again later."
MAX_DEBUG_ATTACHMENT_BYTES = 8 * 1024 * 1024


# Data refresh and background loops
@tasks.loop(minutes=30)
async def revalidate_tokens() -> None:
    try:
        await battlevive_client.refresh_credentials()
    except Exception:
        logger.exception(
            "Failed to revalidate Battlevive credentials, retrying next cycle."
        )
        return
    logger.debug("Token revalidation cycle completed successfully.")


@revalidate_tokens.error
async def revalidate_tokens_error(error: Exception) -> None:
    logger.error("revalidate_tokens loop stopped due to unhandled exception: %s", error)


@tasks.loop(hours=1)
async def refresh_infrequently_changing_data() -> None:
    global battlevive_users

    try:
        fetched_users = await battlevive_client.get_users()
    except Exception:
        logger.exception("Failed to refresh Battlevive users, skipping this cycle.")
        return

    try:
        await sync_battlevive_data_to_db(users=fetched_users)
    except Exception:
        logger.exception("Failed to sync Battlevive users, skipping role sync.")
        return

    battlevive_users = fetched_users
    logger.debug("Refreshed %d users.", len(battlevive_users))

    try:
        await give_battlevive_role(bot)
    except Exception:
        logger.exception("Failed to sync Battlevive player roles this cycle.")


@refresh_infrequently_changing_data.error
async def refresh_infrequently_changing_data_error(error: Exception) -> None:
    logger.error(
        "refresh_infrequently_changing_data stopped due to unhandled exception: %s",
        error,
    )


@tasks.loop(seconds=30)
async def refresh_frequently_changing_data() -> None:
    global battlevive_users
    global lobbies
    global season_ratings

    try:
        fetched_lobbies, fetched_season_ratings = await asyncio.gather(
            battlevive_client.get_lobbies(),
            battlevive_client.get_season_ratings(),
        )
    except Exception:
        logger.exception(
            "Failed to refresh Battlevive lobbies and ratings, skipping this cycle."
        )
        return

    try:
        await sync_battlevive_data_to_db(
            lobbies=fetched_lobbies,
            season_ratings=fetched_season_ratings,
        )
    except MissingUsersError:
        logger.warning("Fetched data contains new users; refreshing users and retrying.")
        try:
            fetched_users = await battlevive_client.get_users()
            await sync_battlevive_data_to_db(
                users=fetched_users,
                lobbies=fetched_lobbies,
                season_ratings=fetched_season_ratings,
            )
        except Exception:
            logger.exception("Failed to refresh missing users, skipping this cycle.")
            return

        battlevive_users = fetched_users
        try:
            await give_battlevive_role(bot)
        except Exception:
            logger.exception("Failed to sync Battlevive player roles.")
    except Exception:
        logger.exception("Failed to sync Battlevive lobbies and ratings.")
        return

    lobbies = fetched_lobbies
    season_ratings = fetched_season_ratings

    if bot.active_lobby_service is not None:
        bot.active_lobby_service.request_reconciliation()

    try:
        await give_rank_roles(bot)
    except Exception:
        logger.exception("Failed to sync rank roles.")


@refresh_frequently_changing_data.error
async def refresh_frequently_changing_data_error(error: Exception) -> None:
    logger.error(
        "refresh_frequently_changing_data stopped due to unhandled exception: %s",
        error,
    )

# Bot setup
intents = discord.Intents.default()
intents.members = True


class BattleviveBot(commands.Bot):
    leaderboard_service: LeaderboardService | None = None
    active_lobby_service: ActiveLobbyService | None = None

    async def close(self) -> None:
        if revalidate_tokens.is_running():
            revalidate_tokens.cancel()
        if refresh_infrequently_changing_data.is_running():
            refresh_infrequently_changing_data.cancel()
        if refresh_frequently_changing_data.is_running():
            refresh_frequently_changing_data.cancel()
        if self.leaderboard_service is not None:
            await self.leaderboard_service.stop()
            self.leaderboard_service = None
        if self.active_lobby_service is not None:
            await self.active_lobby_service.stop()
            self.active_lobby_service = None
        await battlevive_client.close()
        await close_pool()
        await super().close()


bot = BattleviveBot(command_prefix=(), intents=intents)


# Events
@bot.event
async def setup_hook() -> None:
    await init_pool(DATABASE_URL)
    bot.leaderboard_service = LeaderboardService(bot, DATABASE_URL)
    bot.leaderboard_service.start()
    bot.active_lobby_service = ActiveLobbyService(
        bot,
        battlevive_client,
        db,
        DATABASE_URL,
        BATTLEVIVE_URL,
        ASSETS_DIR,
    )
    bot.active_lobby_service.start()

    if DISCORD_COMMAND_GUILD_ID is None:
        await bot.tree.sync()
        logger.info("Synchronized Discord commands globally.")
    else:
        guild = discord.Object(id=DISCORD_COMMAND_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logger.info(
            "Synchronized Discord commands to development guild %s.",
            DISCORD_COMMAND_GUILD_ID,
        )

    revalidate_tokens.start()
    refresh_infrequently_changing_data.start()
    refresh_frequently_changing_data.start()


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    service = bot.leaderboard_service
    if service is not None:
        service.request_reconciliation()
    active_lobby_service = bot.active_lobby_service
    if active_lobby_service is not None:
        active_lobby_service.request_reconciliation()


@bot.event
async def on_member_join(member: discord.Member) -> None:
    try:
        await reconcile_member_roles(member)
    except (discord.Forbidden, discord.HTTPException):
        logger.exception(
            "Could not reconcile roles for joining member %s in guild %s.",
            member.id,
            member.guild.id,
        )
    service = bot.leaderboard_service
    if service is not None:
        service.request_reconciliation()


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    service = bot.leaderboard_service
    if service is not None:
        service.request_reconciliation()


# Commands

# Discord configuration commands
config_group = app_commands.Group(
    name="config",
    description="Configure Battlevive bot settings for this server",
)
config_leaderboard_group = app_commands.Group(
    name="leaderboard",
    description="Configure leaderboard settings",
    parent=config_group,
)
config_active_lobbies_group = app_commands.Group(
    name="active_lobbies",
    description="Configure active-lobby posts",
    parent=config_group,
)
config_reset_group = app_commands.Group(
    name="reset",
    description="Reset Battlevive bot settings",
    parent=config_group,
)


async def _check_config_access(interaction: discord.Interaction) -> bool:
    return await _check_guild_permission(
        interaction,
        permission="manage_guild",
        permission_name="Manage Server",
        action="change bot configuration",
    )


async def _check_guild_permission(
    interaction: discord.Interaction,
    *,
    permission: str,
    permission_name: str,
    action: str,
) -> bool:
    guild = getattr(interaction, "guild", None)
    if guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return False

    user = getattr(interaction, "user", None)
    guild_permissions = getattr(user, "guild_permissions", None)
    if not getattr(guild_permissions, permission, False):
        await interaction.response.send_message(
            f"You need the {permission_name} permission to {action}.",
            ephemeral=True,
        )
        return False

    return True


async def _check_debug_access(interaction: discord.Interaction) -> bool:
    if not await _check_guild_permission(
        interaction,
        permission="manage_guild",
        permission_name="Manage Server",
        action="export debug data",
    ):
        return False
    config = await db.get_guild_config(interaction.guild.id)
    if config is None or not config["debug_commands_enabled"]:
        await interaction.response.send_message(
            "Debug exports are disabled for this server.",
            ephemeral=True,
        )
        return False
    return True


def _config_channel_permissions(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False

    permissions = channel.permissions_for(bot_member)
    return (
        permissions.view_channel
        and permissions.send_messages
        and permissions.attach_files
        and permissions.read_message_history
    )


def _active_lobby_channel_permissions(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
) -> bool:
    bot_member = guild.me
    if bot_member is None:
        return False

    permissions = channel.permissions_for(bot_member)
    return all(
        (
            permissions.view_channel,
            permissions.send_messages,
            permissions.embed_links,
            permissions.attach_files,
            permissions.read_message_history,
            permissions.mention_everyone,
        )
    )


def _safe_notification_role(
    guild: discord.Guild,
    role: discord.Role,
) -> bool:
    is_default = getattr(role, "is_default", None)
    return not (
        role == guild.default_role
        or (callable(is_default) and is_default())
        or getattr(role, "managed", False)
    )


async def _send_config_failure(
    interaction: discord.Interaction,
    message: str,
) -> None:
    logger.exception(message)
    if not interaction.response.is_done():
        await interaction.response.send_message(
            SAFE_COMMAND_ERROR,
            ephemeral=True,
        )


@config_leaderboard_group.command(
    name="channel",
    description="Set the channel for future leaderboard posts",
)
@app_commands.describe(channel="Text or news channel for leaderboard posts")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_leaderboard_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    if not await _check_config_access(interaction):
        return

    if channel.type not in (discord.ChannelType.text, discord.ChannelType.news):
        await interaction.response.send_message(
            "Please choose a text or news channel.",
            ephemeral=True,
        )
        return

    if not _config_channel_permissions(channel, interaction.guild):
        await interaction.response.send_message(
            "I need View Channel, Send Messages, Attach Files, and Read Message History "
            "permissions in that channel.",
            ephemeral=True,
        )
        return

    try:
        await db.upsert_guild_config(
            interaction.guild.id,
            channel.id,
            interaction.user.id,
        )
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        await interaction.response.send_message(
            f"Leaderboard channel set to {channel}.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config leaderboard channel failed")


@config_leaderboard_group.command(
    name="limit",
    description="Set leaderboard places from 1-50, or omit for the maximum",
)
@app_commands.describe(amount="Number of places from 1-50; omit for the maximum of 50")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_leaderboard_limit(
    interaction: discord.Interaction,
    amount: int | None = None,
) -> None:
    if not await _check_config_access(interaction):
        return
    if amount is not None and not (1 <= amount <= LEADERBOARD_MAX_ENTRIES):
        await interaction.response.send_message(
            f"Leaderboard limit must be between 1 and "
            f"{LEADERBOARD_MAX_ENTRIES}.",
            ephemeral=True,
        )
        return

    try:
        await db.set_leaderboard_limit(
            interaction.guild.id,
            amount,
            interaction.user.id,
        )
        config = await db.get_guild_config(interaction.guild.id)
        if (
            config is not None
            and config["leaderboard_channel_id"] is not None
            and bot.leaderboard_service is not None
        ):
            bot.leaderboard_service.request_reconciliation()
        display = (
            str(amount)
            if amount is not None
            else f"{LEADERBOARD_MAX_ENTRIES} (maximum)"
        )
        await interaction.response.send_message(
            f"Leaderboard limit set to {display}.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config leaderboard limit failed")


@config_active_lobbies_group.command(
    name="channel",
    description="Set the channel for active-lobby posts",
)
@app_commands.describe(channel="Text or news channel for active-lobby posts")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_active_lobbies_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    if not await _check_config_access(interaction):
        return
    if channel.type not in (discord.ChannelType.text, discord.ChannelType.news):
        await interaction.response.send_message(
            "Please choose a text or news channel.",
            ephemeral=True,
        )
        return
    if not _active_lobby_channel_permissions(channel, interaction.guild):
        await interaction.response.send_message(
            "I need View Channel, Send Messages, Embed Links, Attach Files, "
            "Read Message History, and Mention @everyone, @here, and All Roles "
            "permissions in that channel.",
            ephemeral=True,
        )
        return

    try:
        await db.set_active_lobby_channel(
            interaction.guild.id,
            channel.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        await interaction.response.send_message(
            f"Active-lobby channel set to {channel}. Existing lobbies will be posted silently.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config active lobbies channel failed")


@config_active_lobbies_group.command(
    name="role",
    description="Set the active-lobby notification role, or omit for the default",
)
@app_commands.describe(role="Safe role to notify; omit to use the generated Active Lobby role")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_active_lobbies_role(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    if not await _check_config_access(interaction):
        return

    selected = role or discord.utils.get(
        interaction.guild.roles,
        name=ACTIVE_LOBBY_ROLE,
    )
    if selected is None:
        await interaction.response.send_message(
            "The Active Lobby role does not exist. Run /create_roles first or choose another role.",
            ephemeral=True,
        )
        return
    if not _safe_notification_role(interaction.guild, selected):
        await interaction.response.send_message(
            "Choose a non-default role that is not managed by an integration.",
            ephemeral=True,
        )
        return

    try:
        await db.set_active_lobby_role(
            interaction.guild.id,
            selected.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        await interaction.response.send_message(
            f"Active-lobby notifications will mention {selected.mention}.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config active lobbies role failed")


@config_active_lobbies_group.command(
    name="moderator_role",
    description="Set the disputed-game moderator role, or omit for the default",
)
@app_commands.describe(
    role="Safe role to notify; omit to use the generated Website Moderator role"
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_active_lobbies_moderator_role(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    if not await _check_config_access(interaction):
        return

    selected = role or discord.utils.get(
        interaction.guild.roles,
        name=WEBSITE_MODERATOR_ROLE,
    )
    if selected is None:
        await interaction.response.send_message(
            "The Website Moderator role does not exist. Run /create_roles first or choose another role.",
            ephemeral=True,
        )
        return
    if not _safe_notification_role(interaction.guild, selected):
        await interaction.response.send_message(
            "Choose a non-default role that is not managed by an integration.",
            ephemeral=True,
        )
        return

    try:
        await db.set_website_moderator_role(
            interaction.guild.id,
            selected.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        await interaction.response.send_message(
            f"Disputed-game notifications will mention {selected.mention}.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(
            interaction,
            "config active lobbies moderator role failed",
        )


@config_reset_group.command(
    name="leaderboard",
    description="Reset the leaderboard configuration",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_reset_leaderboard(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return

    try:
        await db.reset_guild_config(interaction.guild.id, interaction.user.id)
        if bot.leaderboard_service is not None:
            bot.leaderboard_service.request_reconciliation()
        await interaction.response.send_message(
            "Leaderboard configuration reset.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config reset leaderboard failed")


@config_reset_group.command(
    name="active_lobbies",
    description="Reset active-lobby configuration and remove its posts",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_reset_active_lobbies(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        await db.reset_active_lobby_config(
            interaction.guild.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        await interaction.response.send_message(
            "Active-lobby configuration reset. Stored notification history is retained while posts are cleaned up.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config reset active lobbies failed")


@config_group.command(
    name="debug",
    description="Enable or disable administrator debug exports",
)
@app_commands.describe(enabled="Whether debug export commands are enabled")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_debug(
    interaction: discord.Interaction,
    enabled: bool,
) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        await db.set_debug_commands_enabled(
            interaction.guild.id,
            enabled,
            interaction.user.id,
        )
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"Debug exports {status} for this server.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, "config debug failed")


@config_group.command(name="show", description="Show this server's bot configuration")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_show(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return

    try:
        config = await db.get_guild_config(interaction.guild.id)
        channel_id = config["leaderboard_channel_id"] if config else None
        channel_message = (
            f"Leaderboard channel: <#{channel_id}>."
            if channel_id is not None
            else "Leaderboard channel: not configured."
        )
        limit = config["leaderboard_limit"] if config else None
        limit_display = (
            str(limit)
            if limit is not None
            else f"{LEADERBOARD_MAX_ENTRIES} (maximum)"
        )
        limit_message = f"Leaderboard limit: {limit_display}."
        debug_enabled = bool(config and config["debug_commands_enabled"])
        debug_message = (
            "Debug exports: enabled."
            if debug_enabled
            else "Debug exports: disabled."
        )
        active_channel_id = config.get("active_lobby_channel_id") if config else None
        active_role_id = config.get("active_lobby_role_id") if config else None
        moderator_role_id = (
            config.get("website_moderator_role_id") if config else None
        )
        active_channel_message = (
            f"Active-lobby channel: <#{active_channel_id}>."
            if active_channel_id is not None
            else "Active-lobby channel: not configured."
        )
        active_role_message = (
            f"Active-lobby notification role: <@&{active_role_id}>."
            if active_role_id is not None
            else "Active-lobby notification role: not configured."
        )
        moderator_role_message = (
            f"Website moderator role: <@&{moderator_role_id}>."
            if moderator_role_id is not None
            else "Website moderator role: not configured."
        )
        message = (
            f"{channel_message}\n{limit_message}\n{active_channel_message}\n"
            f"{active_role_message}\n{moderator_role_message}\n{debug_message}"
        )
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config show failed")


bot.tree.add_command(config_group)


@bot.tree.command(name="create_roles", description="Create required roles")
@app_commands.default_permissions(manage_roles=True)
@app_commands.guild_only()
async def create_roles_slash(interaction: discord.Interaction) -> None:
    if not await _check_guild_permission(
        interaction,
        permission="manage_roles",
        permission_name="Manage Roles",
        action="create Battlevive roles",
    ):
        return
    bot_member = interaction.guild.me
    if (
        bot_member is None
        or not bot_member.guild_permissions.manage_roles
    ):
        await interaction.response.send_message(
            "I need the Manage Roles permission to create Battlevive roles.",
            ephemeral=True,
        )
        return

    logger.info(
        "create_roles called by %s in guild '%s' (%s)",
        interaction.user,
        interaction.guild.name,
        interaction.guild.id,
    )
    try:
        result = await create_roles(interaction.guild)
        config = await db.get_guild_config(interaction.guild.id)
        role_defaults = (
            (
                ACTIVE_LOBBY_ROLE,
                config.get("active_lobby_role_id") if config is not None else None,
                db.set_active_lobby_role,
            ),
            (
                WEBSITE_MODERATOR_ROLE,
                config.get("website_moderator_role_id") if config is not None else None,
                db.set_website_moderator_role,
            ),
        )
        config_changed = False
        for role_name, configured_role_id, setter in role_defaults:
            default_notification_role = result.safe_roles.get(role_name)
            if (
                default_notification_role is None
                and role_name in result.existing
                and role_name not in result.rejected
                and role_name not in result.failed
            ):
                default_notification_role = discord.utils.get(
                    interaction.guild.roles,
                    name=role_name,
                )
            if (
                configured_role_id is None
                and default_notification_role is not None
                and role_name not in result.rejected
                and role_name not in result.failed
                and not getattr(default_notification_role, "mentionable", False)
                and _safe_notification_role(
                    interaction.guild,
                    default_notification_role,
                )
            ):
                await setter(
                    interaction.guild.id,
                    default_notification_role.id,
                    interaction.user.id,
                )
                config_changed = True
        if config_changed and bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        message = (
            f"Role setup complete: {len(result.created)} created, "
            f"{len(result.existing)} already present, "
            f"{len(result.rejected)} unsafe existing roles rejected, "
            f"{len(result.failed)} failed."
        )
        issues = {**result.rejected, **result.failed}
        if issues:
            issue_summary = "; ".join(
                f"{name}: {reason}" for name, reason in issues.items()
            )
            message = f"{message}\nIssues: {issue_summary}"
        await interaction.response.send_message(
            message,
            ephemeral=bool(result.rejected or result.failed),
        )
    except Exception:
        logger.exception(
            "create_roles command failed for guild '%s' (%s)",
            interaction.guild.name,
            interaction.guild.id,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                SAFE_COMMAND_ERROR,
                ephemeral=True,
            )


def _json_attachment(name: str, records: list[object]) -> tuple[BytesIO, discord.File]:
    buffer = BytesIO()
    encoder = json.JSONEncoder(indent=2, default=str)
    try:
        for chunk in encoder.iterencode(records):
            encoded_chunk = chunk.encode("utf-8")
            if buffer.tell() + len(encoded_chunk) > MAX_DEBUG_ATTACHMENT_BYTES:
                raise ValueError(
                    "Debug export attachment exceeds the safe size limit"
                )
            buffer.write(encoded_chunk)
        buffer.seek(0)
        return buffer, discord.File(buffer, filename=name)
    except BaseException:
        buffer.close()
        raise


def _claim_debug_export() -> bool:
    global _debug_export_running
    if _debug_export_running:
        return False
    _debug_export_running = True
    return True


def _release_debug_export() -> None:
    global _debug_export_running
    _debug_export_running = False


async def _send_debug_export(
    interaction: discord.Interaction,
    datasets: tuple[list[object], list[object], list[object]],
) -> None:
    attachments: list[tuple[BytesIO, discord.File]] = []
    try:
        for filename, records in zip(
            ("users.json", "lobbies.json", "ratings.json"),
            datasets,
            strict=True,
        ):
            attachments.append(_json_attachment(filename, records))
        await interaction.followup.send(
            files=[attachment for _, attachment in attachments],
            ephemeral=True,
        )
    finally:
        for buffer, attachment in attachments:
            attachment.close()
            buffer.close()


@bot.tree.command(
    name="debug_get_db_data",
    description="Dump all data from db unformatted",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def debug_get_db_data(interaction: discord.Interaction) -> None:
    try:
        if not await _check_debug_access(interaction):
            return
    except Exception:
        logger.exception("Debug export authorization failed.")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                SAFE_COMMAND_ERROR,
                ephemeral=True,
            )
        return
    if not _claim_debug_export():
        await interaction.response.send_message(
            "Another debug export is already running. Try again shortly.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        db_users, db_lobbies, db_ratings = await asyncio.gather(
            db.get_users(),
            db.get_lobbies(),
            db.get_season_ratings(),
        )
        await _send_debug_export(
            interaction,
            (
                [user.json() for user in db_users],
                [lobby.json() for lobby in db_lobbies],
                [rating.json() for rating in db_ratings],
            ),
        )
    except Exception:
        logger.exception("Database debug export failed.")
        await interaction.followup.send(SAFE_COMMAND_ERROR, ephemeral=True)
    finally:
        _release_debug_export()


@bot.tree.command(
    name="debug_get_battlevive_data",
    description="Dump all data from battlevive unformatted",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def debug_get_battlevive_data(interaction: discord.Interaction) -> None:
    try:
        if not await _check_debug_access(interaction):
            return
    except Exception:
        logger.exception("Debug export authorization failed.")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                SAFE_COMMAND_ERROR,
                ephemeral=True,
            )
        return
    if not _claim_debug_export():
        await interaction.response.send_message(
            "Another debug export is already running. Try again shortly.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        await _send_debug_export(
            interaction,
            (
                [user.json() for user in list(battlevive_users)],
                [lobby.json() for lobby in list(lobbies)],
                [rating.json() for rating in list(season_ratings)],
            ),
        )
    except Exception:
        logger.exception("Battlevive debug export failed.")
        await interaction.followup.send(SAFE_COMMAND_ERROR, ephemeral=True)
    finally:
        _release_debug_export()


@bot.tree.command(
    name="rank",
    description="Display your rank",
)
async def rank_command(interaction: discord.Interaction) -> None:
    logger.info("rank called by %s (%s)", interaction.user, interaction.user.id)
    try:
        pool = get_pool()

        user = await pool.fetchrow(
            """
            SELECT
                users.discord_id,
                users.discord_username,
                users.member_number,
                season_ratings.mmr,
                season_ratings.wins,
                season_ratings.losses
            FROM users
            INNER JOIN season_ratings ON users.id = season_ratings.user_id
            WHERE users.discord_id = $1
            """,
            interaction.user.id,
        )

        if user is None:
            logger.debug("rank: no DB record for discord_id=%s", interaction.user.id)
            await interaction.response.send_message(
                "You are not registered.",
                ephemeral=True,
            )
            return

        mmr = user["mmr"]
        rank_current = SeasonRating.rank(mmr)

        rank_next = rank_current
        mmr_required = mmr
        for index, (threshold, name) in enumerate(SeasonRating.RANKS):
            if mmr >= threshold:
                if index > 0:
                    mmr_required, rank_next = SeasonRating.RANKS[index - 1]
                break

        logger.debug(
            "rank: user=%s mmr=%d current=%s next=%s mmr_required=%d",
            interaction.user.id,
            mmr,
            rank_current,
            rank_next,
            mmr_required,
        )

        avatar_bytes = await interaction.user.display_avatar.with_size(128).read()
        avatar = Image.open(BytesIO(avatar_bytes))

        png = build_card(
            avatar=avatar,
            display_name=interaction.user.display_name,
            rank_current=rank_current,
            rank_next=rank_next,
            mmr_current=mmr,
            mmr_required=mmr_required,
            wins=user["wins"],
            losses=user["losses"],
        )
        file = discord.File(BytesIO(png), filename="profile.png")
        await interaction.response.send_message(file=file)
        logger.info(
            "rank: sent profile card to %s (%s)",
            interaction.user,
            interaction.user.id,
        )
    except Exception:
        logger.exception(
            "rank command failed for %s (%s)",
            interaction.user,
            interaction.user.id,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                SAFE_COMMAND_ERROR,
                ephemeral=True,
            )


@bot.tree.command(
    name="refresh",
    description="Refresh data, roles, and the leaderboard",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def refresh(interaction: discord.Interaction) -> None:
    global battlevive_users
    global season_ratings
    global lobbies
    if not await _check_guild_permission(
        interaction,
        permission="manage_guild",
        permission_name="Manage Server",
        action="refresh Battlevive data",
    ):
        return
    if _manual_refresh_lock.locked():
        await interaction.response.send_message(
            "A manual refresh is already running. Try again shortly.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        async with _manual_refresh_lock:
            fetched_users, fetched_lobbies, fetched_season_ratings = (
                await asyncio.gather(
                    battlevive_client.get_users(),
                    battlevive_client.get_lobbies(),
                    battlevive_client.get_season_ratings(),
                )
            )
            await sync_battlevive_data_to_db(
                fetched_users,
                fetched_lobbies,
                fetched_season_ratings,
            )
            await give_battlevive_role(bot, guild=interaction.guild)
            await give_rank_roles(bot, guild=interaction.guild)

            battlevive_users = fetched_users
            lobbies = fetched_lobbies
            season_ratings = fetched_season_ratings
    except Exception:
        logger.exception("Manual refresh failed.")
        await interaction.followup.send(
            SAFE_COMMAND_ERROR,
            ephemeral=True,
        )
        return

    service = bot.leaderboard_service
    if service is not None:
        service.request_reconciliation()
    active_lobby_service = bot.active_lobby_service
    if active_lobby_service is not None:
        active_lobby_service.request_reconciliation()
    await interaction.followup.send("Battlevive data refreshed.", ephemeral=True)

# Runtime
def run() -> None:
    validate_runtime_settings()
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
