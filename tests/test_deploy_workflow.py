from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "deploy.yml"


def test_failed_ssm_deployments_emit_only_the_structured_failure_stage() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=/battlevive/production/system" in workflow
    assert "Show secret-free SSM failure stage" in workflow
    assert "if: ${{ failure() && steps.command.outputs.command_id != '' }}" in workflow
    assert "BATTLEVIVE_DEPLOY_FAILURE_STAGE=" in workflow
    assert "aws ssm list-command-invocations" in workflow
    assert "aws ssm get-command-invocation" in workflow
