resource "aws_ssm_document" "deploy" {
  name            = "${local.name}-deploy"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Deploy a digest-pinned Battlevive release to this slot"
    parameters = {
      bundleKey = {
        type              = "String"
        allowedPattern    = "^releases/[A-Za-z0-9._/-]+$"
        interpolationType = "ENV_VAR"
      }
      bundleChecksum = {
        type              = "String"
        allowedPattern    = "^[a-f0-9]{64}$"
        interpolationType = "ENV_VAR"
      }
      manifestKey = {
        type              = "String"
        allowedPattern    = "^releases/[A-Za-z0-9._/-]+$"
        interpolationType = "ENV_VAR"
      }
      manifestChecksum = {
        type              = "String"
        allowedPattern    = "^[a-f0-9]{64}$"
        interpolationType = "ENV_VAR"
      }
    }
    mainSteps = [{
      action       = "aws:runShellScript"
      name         = "deploy"
      precondition = { StringEquals = ["platformType", "Linux"] }
      inputs = {
        timeoutSeconds = "600"
        runCommand = [
          "set -euo pipefail",
          "workdir=$(mktemp -d /tmp/battlevive-deploy.XXXXXX)",
          "trap 'rm -rf -- \"$workdir\"' EXIT",
          "bundle=\"$workdir/bundle.tar.gz\"",
          "manifest=\"$workdir/release-manifest.json\"",
          "aws s3 cp \"s3://${var.operations_bucket}/$SSM_bundleKey\" \"$bundle\" --region \"${var.aws_region}\"",
          "aws s3 cp \"s3://${var.operations_bucket}/$SSM_manifestKey\" \"$manifest\" --region \"${var.aws_region}\"",
          "printf '%s  %s\\n' \"$SSM_bundleChecksum\" \"$bundle\" | sha256sum --check",
          "printf '%s  %s\\n' \"$SSM_manifestChecksum\" \"$manifest\" | sha256sum --check",
          "release_dir=/opt/battlevive/releases/$SSM_bundleChecksum",
          "install -d -m 0755 \"$release_dir\"",
          "tar -tzf \"$bundle\" | awk '$0 ~ /^\\// || $0 ~ /(^|\\/)\\.\\.($|\\/)/ { exit 1 }'",
          "tar -xzf \"$bundle\" --no-same-owner -C \"$release_dir\"",
          "BATTLEVIVE_BUNDLE_ROOT=\"$release_dir\" BATTLEVIVE_SLOT=\"${var.slot}\" OPERATIONS_BUCKET=\"${var.operations_bucket}\" AWS_REGION=\"${var.aws_region}\" \"$release_dir/install.sh\"",
          "systemctl restart battlevive-secrets.service",
          "BATTLEVIVE_SLOT=\"${var.slot}\" BATTLEVIVE_MANIFEST_VALIDATOR=/usr/local/libexec/battlevive/release_manifest.py /usr/local/libexec/battlevive/deploy --manifest \"$manifest\" --compose-file \"$release_dir/compose.aws.yaml\"",
        ]
      }
    }]
  })
  tags = local.tags
}
