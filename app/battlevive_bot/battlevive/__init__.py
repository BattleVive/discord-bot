from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from .client import BattleviveClient
    from .supabase import SupabaseTransport
    from .tokens import TokenPair
    from .tokens import TokenStore


__all__ = [
    "BattleviveClient",
    "SupabaseTransport",
    "TokenPair",
    "TokenStore",
]


def __getattr__(name: str) -> Any:
    if name == "BattleviveClient":
        from .client import BattleviveClient

        return BattleviveClient
    if name == "SupabaseTransport":
        from .supabase import SupabaseTransport

        return SupabaseTransport
    if name == "TokenPair":
        from .tokens import TokenPair

        return TokenPair
    if name == "TokenStore":
        from .tokens import TokenStore

        return TokenStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
