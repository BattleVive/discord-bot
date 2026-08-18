"""Regression checks for first-deployment host bootstrap behavior."""

from pathlib import Path


SSM = Path(__file__).resolve().parents[1] / "infra" / "modules" / "production" / "ssm.tf"


def test_deploy_document_bootstraps_missing_host_helper_from_verified_bundle() -> None:
    document = SSM.read_text()

    assert 'if ! test -x /usr/local/libexec/battlevive/deploy; then' in document
    assert r'aws s3 cp \"s3://$SSM_operationsBucket/$SSM_bundleKey\" \"$bootstrap_archive\"' in document
    assert r'''printf '%s  %s\\n' \"$SSM_bundleChecksum\" \"$bootstrap_archive\" | sha256sum -c -''' in document
    assert r'tar -tzf \"$bootstrap_archive\"' in document
    assert r'BATTLEVIVE_BUNDLE_ROOT=\"$bootstrap_dir\" \"$bootstrap_dir/install.sh\"' in document
