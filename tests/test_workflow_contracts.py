"""Regression checks for permission-sensitive GitHub Actions composition."""

from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_release_uses_read_only_reusable_quality_suite() -> None:
    """A release caller must never inherit the badge writer's permission."""
    release = (WORKFLOWS / "release.yml").read_text()
    quality = (WORKFLOWS / "quality.yml").read_text()

    assert "uses: ./.github/workflows/quality.yml" in release
    assert "contents: write" not in quality


def test_published_release_can_be_resumed_from_the_fixed_workflow() -> None:
    """Manual dispatch accepts an existing stable release tag after a CI fix."""
    release = (WORKFLOWS / "release.yml").read_text()

    assert "workflow_dispatch:" in release
    assert "release_tag:" in release
    assert "github.event.release.tag_name || inputs.release_tag" in release
