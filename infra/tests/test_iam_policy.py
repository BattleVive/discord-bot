from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_slot_state_backends_are_exact_and_isolated() -> None:
    blue = (ROOT / "infra/slots/blue/backend.tf").read_text()
    green = (ROOT / "infra/slots/green/backend.tf").read_text()
    assert "battlevive-bot/blue.tfstate" in blue
    assert "battlevive-bot/green.tfstate" in green
    assert "green" not in blue and "blue" not in green


def test_slot_resources_are_scoped_and_have_no_customer_kms() -> None:
    source = "\n".join(path.read_text() for path in (ROOT / "infra/modules/slot").glob("*.tf"))
    assert "Slot = var.slot" in source
    assert 'default     = "/battlevive"' in source
    assert 'parameter${local.parameter_root}/*' in source
    assert "aws_kms_key" not in source
