import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "scripts" / "release" / "plan.py"


def run_planner(payload):
    return subprocess.run(
        [sys.executable, str(PLANNER)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_rejects_prerelease_and_noncanonical_stable_tags():
    for tag in ("v1.2.3-rc.1", "1.2.3", "v01.2.3", "v1.2"):
        result = run_planner({"tag": tag, "sha": "a" * 40, "releases": []})
        assert result.returncode == 2
        assert "stable release tag" in result.stderr


def test_new_highest_release_selects_all_semver_aliases_and_deploys():
    result = run_planner(
        {
            "tag": "v1.5.1",
            "sha": "a" * 40,
            "releases": ["v1.4.9", "v1.5.0", "v1.5.1"],
            "production_version": "1.5.0",
            "existing_tags": {},
        }
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "version": "1.5.1",
        "tags": ["1.5.1", "sha-" + "a" * 40, "1.5", "1", "latest"],
        "deploy": True,
        "reuse_exact": False,
        "reuse_sha": False,
    }


def test_backport_updates_minor_only_and_never_downgrades_production():
    result = run_planner(
        {
            "tag": "v1.4.2",
            "sha": "b" * 40,
            "releases": ["v1.4.1", "v1.5.1", "draft", "v2.0.0-rc.1"],
            "production_version": "1.5.1",
            "existing_tags": {},
        }
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tags"] == [
        "1.4.2",
        "sha-" + "b" * 40,
        "1.4",
    ]
    assert json.loads(result.stdout)["deploy"] is False


def test_newest_release_in_older_major_updates_major_but_not_latest():
    result = run_planner(
        {
            "tag": "v1.6.0",
            "sha": "e" * 40,
            "releases": ["v1.5.1", "v2.0.0"],
            "production_version": "2.0.0",
            "existing_tags": {},
        }
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tags"] == [
        "1.6.0", "sha-" + "e" * 40, "1.6", "1"
    ]
    assert json.loads(result.stdout)["deploy"] is False


def test_existing_immutable_tags_are_reusable_only_for_same_revision():
    same = {
        "tag": "v1.2.3",
        "sha": "c" * 40,
        "releases": [],
        "existing_tags": {"1.2.3": "c" * 40, "sha-" + "c" * 40: "c" * 40},
    }
    result = run_planner(same)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["reuse_exact"] is True
    assert output["reuse_sha"] is True

    changed = same | {"existing_tags": {"1.2.3": "d" * 40}}
    result = run_planner(changed)
    assert result.returncode == 3
    assert "immutable tag 1.2.3" in result.stderr


def test_sha_must_be_full_lowercase_commit_id():
    result = run_planner({"tag": "v1.2.3", "sha": "ABC", "releases": []})
    assert result.returncode == 2
    assert "full lowercase commit SHA" in result.stderr
