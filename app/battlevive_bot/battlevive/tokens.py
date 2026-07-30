from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str

    @classmethod
    def from_values(
        cls,
        access_token: object,
        refresh_token: object,
    ) -> TokenPair | None:
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(refresh_token, str)
            or not refresh_token
        ):
            return None
        return cls(access_token=access_token, refresh_token=refresh_token)


class TokenStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> TokenPair | None:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                record = json.load(file)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

        if not isinstance(record, dict):
            return None
        return TokenPair.from_values(
            record.get("access_token"),
            record.get("refresh_token"),
        )

    def save(self, tokens: TokenPair) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)

        try:
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
                file_descriptor = -1
                json.dump(
                    {
                        "access_token": tokens.access_token,
                        "refresh_token": tokens.refresh_token,
                    },
                    file,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self.path)
        except BaseException:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise
