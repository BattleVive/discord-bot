from pathlib import Path


STORAGE = Path(__file__).parents[1] / "infra" / "modules" / "production" / "storage.tf"


def test_supabase_url_is_an_operator_managed_non_secret_parameter() -> None:
    storage = STORAGE.read_text(encoding="utf-8")

    assert 'name  = "${local.parameter_root}/config/supabase-url"' in storage
    assert "lifecycle { ignore_changes = [value] }" in storage
