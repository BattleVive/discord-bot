"""PostgreSQL repository operations for durable gateway state."""
from __future__ import annotations

from collections.abc import Mapping
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
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def update(self, guild_id: int, version: int, changes: Mapping[str, object], *, updated_by: int) -> int:
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
        await self._connection.execute(
            "INSERT INTO guild_config(guild_id, updated_by) VALUES($1, $2) ON CONFLICT(guild_id) DO NOTHING",
            guild_id, updated_by,
        )

    async def get(self, guild_id: int) -> dict[str, object] | None:
        row = await self._connection.fetchrow("SELECT * FROM guild_config WHERE guild_id=$1", guild_id)
        return None if row is None else dict(row)

    async def configured_guides(self) -> list[dict[str, object]]:
        rows = await self._connection.fetch(
            "SELECT * FROM guild_config WHERE guide_forum_channel_id IS NOT NULL"
        )
        return [dict(row) for row in rows]

    async def configured_leaderboards(self) -> list[dict[str, object]]:
        rows = await self._connection.fetch(
            "SELECT * FROM guild_config WHERE leaderboard_channel_id IS NOT NULL"
        )
        return [dict(row) for row in rows]


class PublicationRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert(self, guild_id: int, feature: str, publication_key: str, channel_id: int,
                     message_id: int | None, thread_id: int | None, fingerprint: str | None,
                     metadata: Mapping[str, object]) -> None:
        await self._connection.execute(
            """INSERT INTO discord_publications(guild_id,feature,publication_key,channel_id,message_id,thread_id,fingerprint,metadata)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT(guild_id,feature,publication_key) DO UPDATE SET channel_id=EXCLUDED.channel_id,
            message_id=EXCLUDED.message_id,thread_id=EXCLUDED.thread_id,fingerprint=EXCLUDED.fingerprint,
            metadata=EXCLUDED.metadata,version=discord_publications.version+1,updated_at=NOW()""",
            guild_id, feature, publication_key, channel_id, message_id, thread_id, fingerprint, __import__("json").dumps(metadata),
        )

    async def list_for_feature(self, guild_id: int, feature: str) -> list[dict[str, object]]:
        rows = await self._connection.fetch(
            "SELECT publication_key, channel_id, message_id, thread_id, fingerprint, metadata FROM discord_publications WHERE guild_id=$1 AND feature=$2",
            guild_id, feature,
        )
        return [dict(row) for row in rows]

    async def delete(self, guild_id: int, feature: str, publication_key: str) -> None:
        await self._connection.execute(
            "DELETE FROM discord_publications WHERE guild_id=$1 AND feature=$2 AND publication_key=$3",
            guild_id, feature, publication_key,
        )


class IdentityRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def bind(self, member_number: int, discord_id: int, provenance: str) -> bool:
        if member_number <= 0 or discord_id <= 0 or not provenance:
            raise ValueError("identity binding values must be positive and have provenance")
        owner = await self._connection.fetchval(
            "SELECT member_number FROM identity_links WHERE discord_id=$1", discord_id
        )
        if owner is not None and int(owner) != member_number:
            return False
        result = await self._connection.execute(
            """INSERT INTO identity_links(member_number,discord_id,provenance) VALUES($1,$2,$3)
            ON CONFLICT(member_number) DO UPDATE SET discord_id=EXCLUDED.discord_id,
            provenance=EXCLUDED.provenance,updated_at=NOW()""",
            member_number, discord_id, provenance,
        )
        return result in {"INSERT 0 1", "UPDATE 1"}

    async def discord_ids(self, member_numbers: list[int]) -> dict[int, int]:
        if not member_numbers:
            return {}
        rows = await self._connection.fetch(
            "SELECT member_number, discord_id FROM identity_links WHERE member_number = ANY($1::bigint[])",
            member_numbers,
        )
        return {int(row["member_number"]): int(row["discord_id"]) for row in rows}

    async def member_number_for_discord_id(self, discord_id: int) -> int | None:
        value = await self._connection.fetchval(
            "SELECT member_number FROM identity_links WHERE discord_id=$1", discord_id
        )
        return int(value) if value is not None else None


class RuleRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def set(self, guild_id: int, command_name: str, channel_id: int, allowed: bool) -> None:
        await self._connection.execute(
            """INSERT INTO command_channel_rules(guild_id,command_name,channel_id,allowed) VALUES($1,$2,$3,$4)
            ON CONFLICT(guild_id,command_name,channel_id) DO UPDATE SET allowed=EXCLUDED.allowed""",
            guild_id, command_name, channel_id, allowed,
        )

    async def remove(self, guild_id: int, command_name: str, channel_id: int) -> None:
        await self._connection.execute(
            "DELETE FROM command_channel_rules WHERE guild_id=$1 AND command_name=$2 AND channel_id=$3",
            guild_id, command_name, channel_id,
        )

    async def list(self, guild_id: int) -> list[dict[str, object]]:
        rows = await self._connection.fetch(
            "SELECT command_name, channel_id, allowed FROM command_channel_rules WHERE guild_id=$1 ORDER BY command_name, channel_id",
            guild_id,
        )
        return [dict(row) for row in rows]

    async def allows(self, guild_id: int, command_name: str, channel_id: int) -> bool:
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
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def claim(self, guild_id: int, purpose: str, logical_name: str, role_id: int) -> None:
        await self._connection.execute(
            """INSERT INTO created_roles(guild_id,purpose,logical_name,role_id) VALUES($1,$2,$3,$4)
            ON CONFLICT(guild_id,purpose,logical_name) DO UPDATE SET role_id=EXCLUDED.role_id,updated_at=NOW()""",
            guild_id, purpose, logical_name, role_id,
        )

    async def is_owned(self, guild_id: int, role_id: int) -> bool:
        return bool(await self._connection.fetchval(
            "SELECT 1 FROM created_roles WHERE guild_id=$1 AND role_id=$2", guild_id, role_id
        ))
