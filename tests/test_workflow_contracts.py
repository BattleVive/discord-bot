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


def test_release_plan_uses_the_workflow_revision_for_release_tooling() -> None:
    """A recovered release must not run a stale helper from the release tag."""
    release = (WORKFLOWS / "release.yml").read_text()
    plan = release.split("  plan:\n", 1)[1].split("  publish:\n", 1)[0]

    assert "name: Check out release tooling" in plan
    assert "ref: ${{ github.workflow_sha }}" in plan
    assert "ref: ${{ needs.validate.outputs.sha }}" not in plan
