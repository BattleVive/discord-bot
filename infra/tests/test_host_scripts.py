from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_aws_compose_uses_external_rds_not_local_postgres() -> None:
    compose = (ROOT / "docker-compose.aws.yml").read_text()
    assert "postgres:" not in compose
    assert "DATABASE_URL_FILE" in compose
    assert 'battlevive_gateway.migrations' in compose


def test_local_compose_retains_postgres_for_development() -> None:
    assert "postgres:" in (ROOT / "docker-compose.yml").read_text()
