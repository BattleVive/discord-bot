from __future__ import annotations

import ast
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_setup_helper_leaves_rotating_token_storage_to_the_runtime() -> None:
    source = (ROOT_DIR / "utils" / "env_gen.py").read_text(encoding="utf-8")

    assert "TokenStore(TOKEN_FILE).save(tokens)" not in source


def test_setup_helper_writes_bootstrap_tokens_to_the_compose_env_file() -> None:
    source = (ROOT_DIR / "utils" / "env_gen.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "set_key"
    ]

    assert any(
        len(call.args) >= 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "UTILS_ENV"
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value == "BOOTSTRAP_JWT"
        for call in calls
    )
