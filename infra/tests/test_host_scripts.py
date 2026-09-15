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
    assert '"$service readiness timed out"' in deploy
    assert "wait_for_healthy upstream-data" in deploy
    assert "wait_for_healthy gateway" in deploy
    assert 'wait_for_healthy upstream-data' in deploy
    assert 'wait_for_healthy gateway' in deploy
    assert 'python3 "$validator" --validate' in deploy
    assert 'docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"' in deploy


def test_secrets_service_loads_the_slot_environment_written_by_install() -> None:
    """Verify systemd supplies the slot identity needed to render secrets."""
    unit = (ROOT / "infra/host/systemd/battlevive-secrets.service").read_text()
    assert "EnvironmentFile=-/run/battlevive/host.env" in unit


def test_secret_renderer_reads_the_database_host_from_slot_configuration() -> None:
    """Verify the managed RDS password secret is paired with its endpoint."""
    renderer = (ROOT / "infra/host/bin/render-secrets.sh").read_text()
    ssm = (ROOT / "infra/modules/slot/ssm.tf").read_text()
    assert 'name  = "${local.parameter_root}/config/database-host"' in ssm
    assert 'name "/battlevive/$BATTLEVIVE_SLOT/config/database-host"' in renderer


def test_local_compose_retains_postgres_for_development() -> None:
    """Verify that local compose retains postgres for development."""
    assert "postgres:" in (ROOT / "docker-compose.yml").read_text()
