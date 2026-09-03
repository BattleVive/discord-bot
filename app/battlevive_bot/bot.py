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
from .battlevive.guides import HttpGuideContentSource
from .battlevive.guides import SupabaseGuideCatalogSource
from .battlevive.supabase import SupabaseTransport
from .battlevive.tokens import build_token_store
from .command_access import BattleviveCommandTree
from .command_access import CommandAccessService
from .db import close_pool
from .db import init_pool
from .db import get_pool
from .health import database_is_ready
from .health import HealthState
from .health import required_services_ready
from .identity import IdentityStatus
from .identity import resolve_member_identity
from .images import build_card
from .leaderboards import LeaderboardService
from .guides import GuideThreadService
from .logs import logger
from .models import Lobby
from .models import SeasonRating
from .models import User
from .roles import ACTIVE_LOBBY_ROLE
from .roles import GUIDE_UPDATES_ROLE
from .roles import create_roles
from .roles import give_rank_roles
from .roles import reconcile_member_roles
from .roles import WEBSITE_MODERATOR_ROLE
from .refresh import RefreshCoordinator
from .refresh import RefreshResult
from .settings import ASSETS_DIR
from .settings import BATTLEVIVE_BOOTSTRAP_JWT
from .settings import BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN
from .settings import BATTLEVIVE_TOKEN_PATH
from .settings import BATTLEVIVE_TOKEN_SSM_PARAMETER
from .settings import BATTLEVIVE_TOKEN_STORE
from .settings import BATTLEVIVE_URL
from .settings import DATABASE_URL
from .settings import DATA_DIR
from .settings import DISCORD_COMMAND_GUILD_ID
from .settings import DISCORD_BOT_TOKEN
from .settings import LEADERBOARD_MAX_ENTRIES
from .settings import SUPABASE_API_KEY
from .settings import SUPABASE_URL
from .settings import AWS_REGION
from .settings import validate_runtime_settings


battlevive_client = BattleviveClient(
    bootstrap_access_token=BATTLEVIVE_BOOTSTRAP_JWT,
    bootstrap_refresh_token=BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN,
    token_path=BATTLEVIVE_TOKEN_PATH,
    supabase_url=SUPABASE_URL,
    supabase_api_key=SUPABASE_API_KEY,
    token_store=build_token_store(
        kind=BATTLEVIVE_TOKEN_STORE,
        path=BATTLEVIVE_TOKEN_PATH,
        parameter_name=BATTLEVIVE_TOKEN_SSM_PARAMETER,
        region_name=AWS_REGION,
    ),
)
health_state = HealthState()

battlevive_users: list[User] = []
lobbies: list[Lobby] = []
season_ratings: list[SeasonRating] = []
_debug_export_running = False

SAFE_COMMAND_ERROR = "The command failed. Please try again later."
MAX_DEBUG_ATTACHMENT_BYTES = 8 * 1024 * 1024


def _publish_refresh(result: RefreshResult) -> None:
    global battlevive_users, lobbies, season_ratings
    if result.users is not None:
        battlevive_users = result.users
    if result.lobbies is not None:
        lobbies = result.lobbies
    if result.ratings is not None:
        season_ratings = result.ratings


refresh_coordinator = RefreshCoordinator(battlevive_client, _publish_refresh)


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
    try:
        await refresh_coordinator.hourly_users_refresh()
    except Exception:
        logger.exception("Failed to sync Battlevive users, skipping role sync.")
        return
    logger.debug("Refreshed %d users.", len(battlevive_users))
    try:
        await give_rank_roles(bot)
    except Exception:
        logger.exception("Failed to sync rank roles this cycle.")


@refresh_infrequently_changing_data.error
async def refresh_infrequently_changing_data_error(error: Exception) -> None:
    logger.error(
        "refresh_infrequently_changing_data stopped due to unhandled exception: %s",
        error,
    )


@tasks.loop(seconds=30)
async def refresh_frequently_changing_data() -> None:
    try:
        await refresh_coordinator.frequent_lobbies_ratings_refresh()
    except Exception:
        logger.exception("Failed to sync Battlevive lobbies and ratings.")
        return

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


@tasks.loop(seconds=30)
async def publish_health() -> None:
    try:
        database_ready = await database_is_ready(get_pool())
    except RuntimeError:
        database_ready = False
    services_ready = required_services_ready(
        (
            revalidate_tokens.is_running(),
            refresh_infrequently_changing_data.is_running(),
            refresh_frequently_changing_data.is_running(),
            bot.leaderboard_service is not None
            and bot.leaderboard_service.is_running(),
            bot.active_lobby_service is not None
            and bot.active_lobby_service.is_running(),
            bot.guide_thread_service is not None
            and bot.guide_thread_service.is_running(),
        )
    )
    health_state.write(
        discord_ready=bot.is_ready(),
        database_ready=database_ready,
        services_ready=services_ready,
        token_persistence_ready=not battlevive_client.persistence_degraded,
    )


@publish_health.error
async def publish_health_error(error: Exception) -> None:
    logger.error("Health heartbeat task stopped due to an unhandled error: %s", error)

# Bot setup
intents = discord.Intents.default()
intents.members = True


class BattleviveBot(commands.Bot):
    leaderboard_service: LeaderboardService | None = None
    active_lobby_service: ActiveLobbyService | None = None
    guide_thread_service: GuideThreadService | None = None
    command_access_service: CommandAccessService | None = None
    refresh_coordinator: RefreshCoordinator = refresh_coordinator

    async def close(self) -> None:
        if publish_health.is_running():
            publish_health.cancel()
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
        if self.guide_thread_service is not None:
            await self.guide_thread_service.stop()
            self.guide_thread_service = None
        await battlevive_client.close()
        await close_pool()
        await super().close()


bot = BattleviveBot(
    command_prefix=(), intents=intents, tree_cls=BattleviveCommandTree
)


# Events
@bot.event
async def setup_hook() -> None:
    await init_pool(DATABASE_URL)
    bot.command_access_service = CommandAccessService()
    bot.leaderboard_service = LeaderboardService(bot, DATABASE_URL)
    bot.leaderboard_service.start()
    try:
        bot.active_lobby_service = ActiveLobbyService(
            bot,
            battlevive_client,
            db,
            DATABASE_URL,
            BATTLEVIVE_URL,
            ASSETS_DIR,
        )
    except ValueError:
        logger.warning("Battlevive match links disabled because their URL is invalid.")
        bot.active_lobby_service = ActiveLobbyService(
            bot,
            battlevive_client,
            db,
            DATABASE_URL,
            None,
            ASSETS_DIR,
        )
    bot.active_lobby_service.start()
    bot.guide_thread_service = GuideThreadService(
        bot,
        db,
        SupabaseGuideCatalogSource(
            SupabaseTransport(SUPABASE_URL, SUPABASE_API_KEY),
            anon_key=SUPABASE_API_KEY,
        ),
        HttpGuideContentSource(),
    )
    bot.guide_thread_service.start()

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
    publish_health.start()


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    service = bot.leaderboard_service
    if service is not None:
        service.request_reconciliation()
    active_lobby_service = bot.active_lobby_service
    if active_lobby_service is not None:
        active_lobby_service.request_reconciliation()
    if bot.guide_thread_service is not None:
        bot.guide_thread_service.request_reconciliation()


@bot.event
async def on_member_join(member: discord.Member) -> None:
    try:
        await reconcile_member_roles(
            member,
            lambda: _refresh_membership_for_member(member),
        )
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


async def _refresh_membership_for_member(member: discord.Member) -> bool:
    async def local_membership_exists() -> bool:
        if await db.get_active_user_by_discord_id(member.id) is not None:
            return True
        candidates = await db.find_active_users_by_names(
            {
                value
                for value in (
                    getattr(member, "name", None),
                    getattr(member, "display_name", None),
                    getattr(member, "global_name", None),
                    getattr(member, "nick", None),
                )
                if value
            }
        )
        return bool(candidates)

    try:
        await bot.refresh_coordinator.users_and_ratings_refresh(
            local_membership_exists
        )
        await give_rank_roles(bot, guild=member.guild)
        return True
    except Exception:
        logger.exception("Membership refresh failed for joining member %s.", member.id)
        return False


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    try:
        await db.remove_command_channel_rule(channel.guild.id, channel.id)
    except Exception:
        logger.exception("Failed to remove command rule for deleted channel %s.", channel.id)


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
config_guides_group = app_commands.Group(
    name="guides", description="Configure guide forum posts", parent=config_group
)
config_reset_group = app_commands.Group(
    name="reset",
    description="Reset Battlevive bot settings",
    parent=config_group,
)
config_rank_group = app_commands.Group(
    name="rank", description="Configure the public rank command", parent=config_group
)
config_commands_group = app_commands.Group(
    name="commands", description="Configure public command channels", parent=config_group
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


def _guide_forum_permissions(channel: discord.ForumChannel, guild: discord.Guild) -> bool:
    member = guild.me
    if member is None:
        return False
    permissions = channel.permissions_for(member)
    return all((permissions.view_channel, permissions.send_messages, permissions.read_message_history, permissions.manage_threads, permissions.mention_everyone))


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
        previous = await db.get_guild_config(interaction.guild.id)
        tracked = await db.get_created_role(interaction.guild.id, "active_lobby")
        await db.set_active_lobby_role(
            interaction.guild.id,
            selected.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        cleanup_issue = None
        previous_id = previous.get("active_lobby_role_id") if previous else None
        if tracked and tracked["role_id"] == previous_id and previous_id != selected.id:
            cleanup_issue = await _delete_owned_role(
                interaction.guild, "active_lobby", previous_id
            )
        message = f"Active-lobby notifications will mention {selected.mention}."
        if cleanup_issue:
            message += " Configuration changed, but generated-role cleanup remains pending."
        await interaction.response.send_message(
            message,
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
        previous = await db.get_guild_config(interaction.guild.id)
        tracked = await db.get_created_role(interaction.guild.id, "website_moderator")
        await db.set_website_moderator_role(
            interaction.guild.id,
            selected.id,
            interaction.user.id,
        )
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        cleanup_issue = None
        previous_id = previous.get("website_moderator_role_id") if previous else None
        if tracked and tracked["role_id"] == previous_id and previous_id != selected.id:
            cleanup_issue = await _delete_owned_role(
                interaction.guild, "website_moderator", previous_id
            )
        message = f"Disputed-game notifications will mention {selected.mention}."
        if cleanup_issue:
            message += " Configuration changed, but generated-role cleanup remains pending."
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(
            interaction,
            "config active lobbies moderator role failed",
        )


@config_guides_group.command(name="channel", description="Set the forum for Battlevive guides")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_guides_channel(interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
    if not await _check_config_access(interaction):
        return
    if not isinstance(channel, discord.ForumChannel):
        await interaction.response.send_message("Please choose a forum channel.", ephemeral=True)
        return
    if not _guide_forum_permissions(channel, interaction.guild):
        await interaction.response.send_message("I need View Channel, Send Messages, Read Message History, Manage Threads, and Mention @everyone, @here, and All Roles permissions in that forum.", ephemeral=True)
        return
    try:
        await db.set_guide_forum_channel(interaction.guild.id, channel.id, interaction.user.id)
        if bot.guide_thread_service is not None:
            bot.guide_thread_service.request_reconciliation()
        await interaction.response.send_message(f"Guide forum set to {channel}.", ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config guides channel failed")


@config_guides_group.command(name="role", description="Set the guide notification role")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_guides_role(interaction: discord.Interaction, role: discord.Role | None = None) -> None:
    if not await _check_config_access(interaction):
        return
    if role is None:
        role = discord.utils.get(interaction.guild.roles, name=GUIDE_UPDATES_ROLE)
        if role is None:
            await interaction.response.send_message("The Guide Updates role does not exist. Run /create_roles first or choose another role.", ephemeral=True)
            return
    if not _safe_notification_role(interaction.guild, role):
        await interaction.response.send_message("Please choose a safe, non-managed role that is not @everyone.", ephemeral=True)
        return
    try:
        await db.set_guide_notification_role(interaction.guild.id, role.id, interaction.user.id)
        if bot.guide_thread_service is not None:
            bot.guide_thread_service.request_reconciliation()
        await interaction.response.send_message(f"Guide notification role set to {role}.", ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config guides role failed")


@config_guides_group.command(name="automatic_deletion", description="Delete guide threads when guides leave the website")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_guides_automatic_deletion(interaction: discord.Interaction, enabled: bool) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        await db.set_guide_auto_delete_on_removal(interaction.guild.id, enabled, interaction.user.id)
        if bot.guide_thread_service is not None:
            bot.guide_thread_service.request_reconciliation()
        await interaction.response.send_message(f"Automatic guide deletion {'enabled' if enabled else 'disabled'}.", ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config guides automatic deletion failed")


@config_reset_group.command(name="guides", description="Archive all managed guide posts and reset guide configuration")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_reset_guides(interaction: discord.Interaction) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        # Reconciliation sees no catalog only after posts are archived; retain rows on failures.
        config = await db.get_guild_config(interaction.guild.id) or {}
        guild = interaction.guild
        for row in await db.get_guide_threads(guild.id):
            try:
                thread = guild.get_thread(row['thread_id']) or await bot.fetch_channel(row['thread_id'])
                await thread.edit(archived=True, locked=False, reason="Guide configuration reset")
            except discord.NotFound:
                pass
            await db.remove_guide_thread(guild.id, row['source_guide_id'])
        await db.reset_guide_config(guild.id, interaction.user.id)
        await interaction.response.send_message("Guide configuration reset; managed guide posts were archived.", ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config reset guides failed")


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
        cleanup_issues = []
        for purpose in _NOTIFICATION_PURPOSES:
            tracked = await db.get_created_role(interaction.guild.id, purpose)
            if tracked:
                issue = await _delete_owned_role(
                    interaction.guild, purpose, tracked["role_id"]
                )
                if issue:
                    cleanup_issues.append(issue)
        message = "Active-lobby configuration reset. Stored notification history is retained while posts are cleaned up."
        if cleanup_issues:
            message += " Configuration reset, but role cleanup remains pending: " + "; ".join(cleanup_issues)
        await interaction.response.send_message(
            message,
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


@config_rank_group.command(name="cooldown", description="Set the /rank cooldown")
@app_commands.describe(seconds="Cooldown in seconds; 0 disables it")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_rank_cooldown(
    interaction: discord.Interaction,
    seconds: int,
) -> None:
    if not await _check_config_access(interaction):
        return
    if not 0 <= seconds <= 3600:
        await interaction.response.send_message(
            "Cooldown must be between 0 and 3600 seconds.", ephemeral=True
        )
        return
    try:
        await db.set_rank_cooldown_seconds(
            interaction.guild.id, seconds, interaction.user.id
        )
        message = (
            "The /rank cooldown is disabled."
            if seconds == 0
            else f"The /rank cooldown is now {seconds} seconds."
        )
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config rank cooldown failed")


async def _set_command_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    rule: str,
) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        await db.set_command_channel_rule(
            interaction.guild.id, channel.id, rule, interaction.user.id
        )
        classification = "whitelisted" if rule == "allow" else "blacklisted"
        await interaction.response.send_message(
            f"{channel.mention} is now {classification} for public commands.",
            ephemeral=True,
        )
    except Exception:
        await _send_config_failure(interaction, f"config commands {rule} failed")


@config_commands_group.command(name="whitelist", description="Allow public commands in a channel")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_commands_whitelist(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    await _set_command_channel(interaction, channel, "allow")


@config_commands_group.command(name="blacklist", description="Block public commands in a channel")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_commands_blacklist(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    await _set_command_channel(interaction, channel, "block")


@config_commands_group.command(name="remove", description="Remove a public-command channel rule")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_commands_remove(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    if not await _check_config_access(interaction):
        return
    try:
        removed = await db.remove_command_channel_rule(interaction.guild.id, channel.id)
        message = (
            f"Removed the public-command rule for {channel.mention}."
            if removed
            else f"{channel.mention} had no public-command rule."
        )
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config commands remove failed")


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
        cooldown = config.get("rank_cooldown_seconds", 20) if config else 20
        cooldown_message = (
            "Rank cooldown: disabled."
            if cooldown == 0
            else f"Rank cooldown: {cooldown} seconds."
        )
        rules = await db.get_command_channel_rules(interaction.guild.id)
        allow_ids = [rule["channel_id"] for rule in rules if rule["rule"] == "allow"]
        block_ids = [rule["channel_id"] for rule in rules if rule["rule"] == "block"]
        allow_message = "Whitelisted channels: " + (
            ", ".join(f"<#{channel_id}>" for channel_id in allow_ids) or "none"
        ) + "."
        block_message = "Blacklisted channels: " + (
            ", ".join(f"<#{channel_id}>" for channel_id in block_ids) or "none"
        ) + "."
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
        guide_channel_id = config.get("guide_forum_channel_id") if config else None
        guide_role_id = config.get("guide_notification_role_id") if config else None
        guide_channel_message = (
            f"Guide forum: <#{guide_channel_id}>."
            if guide_channel_id is not None else "Guide forum: not configured."
        )
        guide_role_message = (
            f"Guide notification role: <@&{guide_role_id}>."
            if guide_role_id is not None else "Guide notification role: not configured."
        )
        guide_deletion_message = "Guide automatic deletion: " + (
            "enabled." if config and config.get("guide_auto_delete_on_removal") else "disabled."
        )
        message = (
            f"{channel_message}\n{limit_message}\n{active_channel_message}\n"
            f"{active_role_message}\n{moderator_role_message}\n{cooldown_message}\n"
            f"{guide_channel_message}\n{guide_role_message}\n{guide_deletion_message}\n"
            f"{allow_message}\n{block_message}\n{debug_message}"
        )
        chunks: list[str] = []
        while message:
            split_at = min(len(message), 1900)
            if split_at < len(message):
                split_at = message.rfind("\n", 0, split_at) or split_at
            chunks.append(message[:split_at])
            message = message[split_at:].lstrip("\n")
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)
    except Exception:
        await _send_config_failure(interaction, "config show failed")


bot.tree.add_command(config_group)


_NOTIFICATION_PURPOSES = {
    "active_lobby": (ACTIVE_LOBBY_ROLE, "active_lobby_role_id", "set_active_lobby_role"),
    "website_moderator": (
        WEBSITE_MODERATOR_ROLE,
        "website_moderator_role_id",
        "set_website_moderator_role",
    ),
    "guide_updates": (GUIDE_UPDATES_ROLE, "guide_notification_role_id", "set_guide_notification_role"),
}


async def _delete_owned_role(
    guild: discord.Guild,
    purpose: str,
    role_id: int,
) -> str | None:
    role = guild.get_role(role_id)
    if role is None:
        await db.forget_created_role(guild.id, purpose, role_id)
        return None
    try:
        await role.delete(reason="Battlevive generated-role cleanup")
    except discord.NotFound:
        pass
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("Generated role %s cleanup remains pending.", role_id)
        return f"{role.name}: cleanup remains pending"
    await db.forget_created_role(guild.id, purpose, role_id)
    return None


async def _ensure_notification_roles(
    interaction: discord.Interaction,
) -> tuple[list[str], list[str]]:
    guild = interaction.guild
    config = await db.get_guild_config(guild.id) or {}
    outcomes: list[str] = []
    issues: list[str] = []
    for purpose, (name, config_key, setter_name) in _NOTIFICATION_PURPOSES.items():
        configured_id = config.get(config_key)
        get_role = getattr(guild, "get_role", None)
        configured_role = (
            get_role(configured_id)
            if configured_id and callable(get_role)
            else discord.utils.get(guild.roles, id=configured_id)
        )
        tracked = await db.get_created_role(guild.id, purpose)
        if configured_role is not None:
            outcomes.append(f"{name}: configured/skipped")
            if tracked and tracked["role_id"] != configured_id:
                issue = await _delete_owned_role(guild, purpose, tracked["role_id"])
                if issue:
                    issues.append(issue)
            continue

        role = (
            get_role(tracked["role_id"])
            if tracked and callable(get_role)
            else discord.utils.get(guild.roles, id=tracked["role_id"])
            if tracked
            else None
        )
        if tracked and role is None:
            await db.forget_created_role(guild.id, purpose, tracked["role_id"])
            tracked = None
        if role is None:
            legacy = discord.utils.get(guild.roles, name=name)
            role = legacy if legacy is not None and _safe_notification_role(guild, legacy) else None
        if role is None:
            role = await guild.create_role(
                name=name,
                permissions=discord.Permissions.none(),
                mentionable=False,
                reason="Battlevive notification-role setup",
            )
            try:
                await db.record_created_role(guild.id, purpose, role.id, interaction.user.id)
            except Exception as error:
                try:
                    await role.delete(reason="Rollback untracked Battlevive role")
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as rollback_error:
                    raise RuntimeError(
                        f"ownership recording failed ({error}); rollback failed ({rollback_error})"
                    ) from error
                raise
            outcomes.append(f"{name}: created")
        else:
            outcomes.append(f"{name}: configured")
        if getattr(role, "mentionable", False):
            role = await role.edit(
                mentionable=False, reason="Battlevive notification-role safety"
            )
        await getattr(db, setter_name)(guild.id, role.id, interaction.user.id)
    return outcomes, issues


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
        await db.ensure_guild_config(interaction.guild.id, interaction.user.id)
        result = await create_roles(interaction.guild)
        notification_outcomes, cleanup_issues = await _ensure_notification_roles(interaction)
        if bot.active_lobby_service is not None:
            bot.active_lobby_service.request_reconciliation()
        message = (
            f"Role setup complete: {len(result.created)} created, "
            f"{len(result.existing)} already present, "
            f"{len(result.rejected)} unsafe existing roles rejected, "
            f"{len(result.failed)} failed."
        )
        message += "\n" + "; ".join(notification_outcomes)
        issues = {**result.rejected, **result.failed}
        if issues:
            issue_summary = "; ".join(
                f"{name}: {reason}" for name, reason in issues.items()
            )
            message = f"{message}\nIssues: {issue_summary}"
        if cleanup_issues:
            message += "\nPending cleanup: " + "; ".join(cleanup_issues)
        await interaction.response.send_message(
            message,
            ephemeral=True,
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
    extras={"public": True, "cooldown_setting": "rank"},
)
@app_commands.guild_only()
async def rank_command(interaction: discord.Interaction) -> None:
    logger.info("rank called by %s (%s)", interaction.user, interaction.user.id)
    try:
        identity = await resolve_member_identity(
            interaction.user,
            lambda: _refresh_membership_for_member(interaction.user),
        )
        if identity.status is IdentityStatus.ABSENT:
            await interaction.response.send_message(
                "You are not registered.",
                ephemeral=True,
            )
            return
        if identity.status is IdentityStatus.AMBIGUOUS:
            await interaction.response.send_message(
                "Your Battlevive identity is ambiguous. Ask a server administrator for help.",
                ephemeral=True,
            )
            return
        if identity.status in {
            IdentityStatus.REFRESH_FAILED,
            IdentityStatus.DATABASE_FAILED,
        } or identity.user is None:
            await interaction.response.send_message(
                "Registration verification is temporarily unavailable. Please try again later.",
                ephemeral=True,
            )
            return

        user = await db.get_current_rank_profile(identity.user["id"])
        if user is None or user["mmr"] is None:
            try:
                await bot.refresh_coordinator.users_and_ratings_refresh()
                user = await db.get_current_rank_profile(identity.user["id"])
            except Exception:
                await interaction.response.send_message(
                    "Registration verification is temporarily unavailable. Please try again later.",
                    ephemeral=True,
                )
                return
        if user is None:
            await interaction.response.send_message(
                "You are not registered.",
                ephemeral=True,
            )
            return
        if user["mmr"] is None:
            await interaction.response.send_message(
                "You are registered, but you do not have a rating in the current season.",
                ephemeral=True,
            )
            return

        try:
            await reconcile_member_roles(interaction.user)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Could not reconcile the invoking member's MMR role.")

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
    if not await _check_guild_permission(
        interaction,
        permission="manage_guild",
        permission_name="Manage Server",
        action="refresh Battlevive data",
    ):
        return
    if bot.refresh_coordinator.lock.locked():
        await interaction.response.send_message(
            "A manual refresh is already running. Try again shortly.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await bot.refresh_coordinator.full_manual_refresh()
        await give_rank_roles(bot, guild=interaction.guild)
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
    if bot.guide_thread_service is not None:
        bot.guide_thread_service.request_reconciliation()
    await interaction.followup.send("Battlevive data refreshed.", ephemeral=True)

# Runtime
def run() -> None:
    validate_runtime_settings()
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
