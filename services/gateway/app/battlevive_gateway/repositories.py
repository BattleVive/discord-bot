"""PostgreSQL repository operations for durable gateway state."""
from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


class ConcurrentUpdateError(RuntimeError):
    """The caller attempted to overwrite a newer configuration revision."""


_CONFIG_COLUMNS = frozenset({
    "leaderboard_channel_id", "leaderboard_limit", "rank_cooldown_seconds",
    "debug_commands_enabled", "active_lobby_channel_id", "active_lobby_role_id",
    "website_moderator_role_id", "active_lobby_baseline_pending",
    "guide_forum_channel_id", "guide_notification_role_id", "guide_auto_delete_on_removal",
})


class GuildConfigRepository:
    """Provide persistence operations for guild configuration data."""

    def __init__(self, connection: Any) -> None:
        """Bind guild configuration operations to a database connection."""
        self._connection = connection

    async def update(self, guild_id: int, version: int, changes: Mapping[str, object], *, updated_by: int) -> int:
        """Apply an allowed, version-checked update and return the new version.

        Raises ``ValueError`` for empty or unsupported changes and
        ``ConcurrentUpdateError`` when the supplied version is no longer current.
        """
        if not changes or not set(changes) <= _CONFIG_COLUMNS:
            raise ValueError("changes must contain supported configuration fields")
        assignments = ", ".join(f"{name} = ${index}" for index, name in enumerate(changes, start=3))
        query = (
            f"UPDATE guild_config SET {assignments}, updated_by = ${len(changes) + 3}, "
            f"updated_at = NOW(), version = version + 1 WHERE guild_id = $1 AND version = $2"
        )
        result = await self._connection.execute(query, guild_id, version, *changes.values(), updated_by)
        if result != "UPDATE 1":
            raise ConcurrentUpdateError("Guild configuration was changed by another request.")
        return version + 1

    async def ensure(self, guild_id: int, updated_by: int) -> None:
        """Ensure a guild has its initial configuration row."""
        await self._connection.execute(
            "INSERT INTO guild_config(guild_id, updated_by) VALUES($1, $2) ON CONFLICT(guild_id) DO NOTHING",
            guild_id, updated_by,
        )

    async def get(self, guild_id: int) -> dict[str, object] | None:
        """Fetch one guild's current configuration."""
        row = await self._connection.fetchrow("SELECT * FROM guild_config WHERE guild_id=$1", guild_id)
        return None if row is None else dict(row)

    async def configured_guides(self) -> list[dict[str, object]]:
        """Return guild configurations that enable guide publication."""
        rows = await self._connection.fetch("SELECT * FROM guild_config WHERE guide_forum_channel_id IS NOT NULL")
        return [dict(row) for row in rows]

    async def configured_leaderboards(self) -> list[dict[str, object]]:
        """Return guild configurations that enable leaderboard publication."""
        rows = await self._connection.fetch("SELECT * FROM guild_config WHERE leaderboard_channel_id IS NOT NULL")
        return [dict(row) for row in rows]

    async def configured_active_lobbies(self) -> list[dict[str, object]]:
        """Return guild configurations that enable active-lobby publication."""
        rows = await self._connection.fetch("SELECT * FROM guild_config WHERE active_lobby_channel_id IS NOT NULL")
        return [dict(row) for row in rows]


class PublicationRepository:
    """Provide persistence operations for Discord publication state."""

    def __init__(self, connection: Any) -> None:
        """Bind publication-state operations to a database connection."""
        self._connection = connection

    async def upsert(self, guild_id: int, feature: str, publication_key: str, channel_id: int,
                     message_id: int | None, thread_id: int | None, fingerprint: str | None,
                     metadata: Mapping[str, object]) -> None:
        """Create or update one tracked publication."""
        await self._connection.execute(
            """INSERT INTO discord_publications(guild_id,feature,publication_key,channel_id,message_id,thread_id,fingerprint,metadata)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT(guild_id,feature,publication_key) DO UPDATE SET channel_id=EXCLUDED.channel_id,
            message_id=EXCLUDED.message_id,thread_id=EXCLUDED.thread_id,fingerprint=EXCLUDED.fingerprint,
            metadata=EXCLUDED.metadata,version=discord_publications.version+1,updated_at=NOW()""",
            guild_id, feature, publication_key, channel_id, message_id, thread_id, fingerprint, json.dumps(metadata),
        )

    async def list_for_feature(self, guild_id: int, feature: str) -> list[dict[str, object]]:
        """List a guild's tracked publications for one feature."""
        rows = await self._connection.fetch(
            "SELECT publication_key, channel_id, message_id, thread_id, fingerprint, metadata FROM discord_publications WHERE guild_id=$1 AND feature=$2",
            guild_id, feature,
        )
        publications: list[dict[str, object]] = []
        for row in rows:
            publication = dict(row)
            metadata = publication.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            publication["metadata"] = metadata if isinstance(metadata, dict) else {}
            publications.append(publication)
        return publications

    async def delete(self, guild_id: int, feature: str, publication_key: str) -> None:
        """Delete one tracked publication state row."""
        await self._connection.execute(
            "DELETE FROM discord_publications WHERE guild_id=$1 AND feature=$2 AND publication_key=$3",
            guild_id, feature, publication_key,
        )

    async def delete_feature(self, guild_id: int, feature: str) -> None:
        """Delete every tracked publication for one resettable guild feature."""
        await self._connection.execute(
            "DELETE FROM discord_publications WHERE guild_id=$1 AND feature=$2", guild_id, feature
        )


class RuleRepository:
    """Provide persistence operations for command-channel rules."""

    def __init__(self, connection: Any) -> None:
        """Bind command-rule operations to a database connection."""
        self._connection = connection

    async def set(self, guild_id: int, command_name: str, channel_id: int, allowed: bool) -> None:
        """Set a guild's allow or deny decision for a command and channel."""
        await self._connection.execute(
            """INSERT INTO command_channel_rules(guild_id,command_name,channel_id,allowed) VALUES($1,$2,$3,$4)
            ON CONFLICT(guild_id,command_name,channel_id) DO UPDATE SET allowed=EXCLUDED.allowed""",
            guild_id, command_name, channel_id, allowed,
        )

    async def remove(self, guild_id: int, command_name: str, channel_id: int) -> None:
        """Remove a guild's decision for a command and channel."""
        await self._connection.execute(
            "DELETE FROM command_channel_rules WHERE guild_id=$1 AND command_name=$2 AND channel_id=$3",
            guild_id, command_name, channel_id,
        )

    async def list(self, guild_id: int) -> list[dict[str, object]]:
        """List a guild's command-channel rules in deterministic order."""
        rows = await self._connection.fetch(
            "SELECT command_name, channel_id, allowed FROM command_channel_rules WHERE guild_id=$1 ORDER BY command_name, channel_id",
            guild_id,
        )
        return [dict(row) for row in rows]

    async def allows(self, guild_id: int, command_name: str, channel_id: int) -> bool:
        """Resolve an exact or wildcard channel rule for a command.

        If the guild has rules for the command or wildcard scope but none for
        this channel, access is denied. With neither rule scope, access remains
        allowed by default.
        """
        value = await self._connection.fetchval(
            "SELECT allowed FROM command_channel_rules WHERE guild_id=$1 AND command_name=$2 AND channel_id=$3",
            guild_id, command_name, channel_id,
        )
        if value is not None:
            return bool(value)
        value = await self._connection.fetchval(
            "SELECT allowed FROM command_channel_rules WHERE guild_id=$1 AND command_name='*' AND channel_id=$2",
            guild_id, channel_id,
        )
        if value is not None:
            return bool(value)
        configured = await self._connection.fetchval(
            "SELECT 1 FROM command_channel_rules WHERE guild_id=$1 AND command_name = ANY($2::text[]) LIMIT 1",
            guild_id, [command_name, "*"],
        )
        return not bool(configured)


class RoleRepository:
    """Provide persistence operations for bot-owned Discord roles."""

    def __init__(self, connection: Any) -> None:
        """Bind role-ownership operations to a database connection."""
        self._connection = connection

    async def claim(self, guild_id: int, purpose: str, logical_name: str, role_id: int) -> None:
        """Record a role as bot-owned for a guild purpose and logical name."""
        await self._connection.execute(
            """INSERT INTO created_roles(guild_id,purpose,logical_name,role_id) VALUES($1,$2,$3,$4)
            ON CONFLICT(guild_id,purpose,logical_name) DO UPDATE SET role_id=EXCLUDED.role_id,updated_at=NOW()""",
            guild_id, purpose, logical_name, role_id,
        )

    async def is_owned(self, guild_id: int, role_id: int) -> bool:
        """Return whether a role is recorded as bot-owned in the guild."""
        return bool(await self._connection.fetchval(
            "SELECT 1 FROM created_roles WHERE guild_id=$1 AND role_id=$2", guild_id, role_id
        ))
