from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
from typing import Protocol


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


class TokenStoreProtocol(Protocol):
    def load(self) -> TokenPair | None: ...

    def save(self, tokens: TokenPair) -> None: ...


class TokenStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> TokenPair | None:
        file_descriptor = -1
        try:
            path_stat = self.path.lstat()
            if not stat.S_ISREG(path_stat.st_mode):
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_descriptor = os.open(self.path, flags)
            opened_stat = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                os.close(file_descriptor)
                file_descriptor = -1
                return None
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "r", encoding="utf-8") as file:
                file_descriptor = -1
                record = json.load(file)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)

        if not isinstance(record, dict):
            return None
        return TokenPair.from_values(
            record.get("access_token"),
            record.get("refresh_token"),
        )

    def save(self, tokens: TokenPair) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing_stat = self.path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing_stat.st_mode):
                raise OSError("Refusing to replace an unsafe token path")
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


class SSMTokenStore:
    """Persist a token pair as one encrypted Parameter Store value."""

    def __init__(
        self,
        parameter_name: str,
        *,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.parameter_name = parameter_name
        if client is None:
            import boto3

            client = boto3.client("ssm", region_name=region_name)
        self._client = client

    def load(self) -> TokenPair:
        try:
            response = self._client.get_parameter(
                Name=self.parameter_name,
                WithDecryption=True,
            )
        except Exception as error:
            raise RuntimeError(
                "Unable to load Battlevive tokens from Parameter Store."
            ) from error
        try:
            parameter = response["Parameter"]
            if parameter.get("Type") != "SecureString":
                raise ValueError
            record = json.loads(parameter["Value"])
            if not isinstance(record, dict):
                raise ValueError
            tokens = TokenPair.from_values(
                record.get("access_token"), record.get("refresh_token")
            )
            if tokens is None:
                raise ValueError
            return tokens
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Parameter Store contains invalid Battlevive token state."
            ) from error

    def save(self, tokens: TokenPair) -> None:
        value = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
            },
            separators=(",", ":"),
        )
        for attempt in range(3):
            try:
                self._client.put_parameter(
                    Name=self.parameter_name,
                    Value=value,
                    Type="SecureString",
                    Overwrite=True,
                )
                return
            except Exception as error:
                if attempt == 2:
                    raise OSError(
                        "Unable to persist Battlevive tokens after three attempts."
                    ) from error


def build_token_store(
    *,
    kind: str,
    path: Path | str,
    parameter_name: str = "",
    region_name: str = "",
    ssm_client: Any | None = None,
) -> TokenStoreProtocol:
    if kind == "file":
        return TokenStore(path)
    if kind == "ssm":
        if not parameter_name or not region_name:
            raise ValueError("SSM token storage requires a parameter name and region.")
        return SSMTokenStore(
            parameter_name,
            region_name=region_name,
            client=ssm_client,
        )
    raise ValueError("Unknown Battlevive token store type.")
