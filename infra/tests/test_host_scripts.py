from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_aws_compose_uses_external_rds_not_local_postgres() -> None:
    """Verify that AWS compose uses external RDS not local postgres."""
    compose = (ROOT / "docker-compose.aws.yml").read_text()
    assert "postgres:" not in compose
    assert "DATABASE_URL_FILE" in compose
    assert 'battlevive_gateway.migrations' in compose
    assert "'http://127.0.0.1:8080/ready'" in compose


def test_slot_deployment_serializes_and_waits_for_gateway_readiness() -> None:
    """Verify that each slot deploy holds a lock until Docker health is ready."""
    deploy = (ROOT / "scripts/deploy/deploy.sh").read_text()
    assert 'flock -n 9' in deploy
    assert 'battlevive-${slot}-deploy.lock' in deploy
    assert 'State.Health.Status' in deploy
    assert 'gateway readiness timed out' in deploy


def test_local_compose_retains_postgres_for_development() -> None:
    """Verify that local compose retains postgres for development."""
    assert "postgres:" in (ROOT / "docker-compose.yml").read_text()
