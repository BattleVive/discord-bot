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


def test_deploy_document_passes_the_host_deploy_contract() -> None:
    document = SSM.read_text()
    deploy_command = next(
        line for line in document.splitlines()
        if 'timeout 295 /usr/local/libexec/battlevive/deploy' in line
    )

    for required_argument in (
        r'--environment \"$SSM_environment\"',
        r'--version \"$SSM_version\"',
        r'--image-digest \"$SSM_imageDigest\"',
        r'--bundle-key \"$SSM_bundleKey\"',
        r'--bundle-checksum \"$SSM_bundleChecksum\"',
        r'--target-selector \"$SSM_targetSelector\"',
        r'--operations-bucket \"$SSM_operationsBucket\"',
        r'--aws-region \"$SSM_awsRegion\"',
    ):
        assert required_argument in deploy_command
