# Battlevive production infrastructure

Terraform adopts the existing `eu-north-1` EC2 host and adds release operations around it. It does not contain live IDs, ARNs, email addresses, secret values, or Terraform variables generated from secrets.

## User prerequisites

Before any live action:

1. Enable IAM Identity Center, create a temporary bootstrap permission set capable of creating the final scoped roles, and configure the `battlevive-prod` CLI profile: [AWS IAM Identity Center CLI setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).
2. Prepare a Docker Hub read/write automation token: [Docker access tokens](https://docs.docker.com/security/access-tokens/).
3. Choose the operations alert email. Terraform creates an SNS subscription; immediately confirm the AWS email: [SNS email confirmation](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html).
4. Plan to configure GitHub variables, secrets, and protected environments after Terraform outputs the role ARNs: [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

Run `infra/tools/bootstrap-toolbox.sh` to create the `aws` Toolbox and install AWS CLI v2, the Session Manager plugin, Terraform, `jq`, ShellCheck, pytest, TFLint, and Trivy. Authenticate explicitly with `aws sso login --profile battlevive-prod`; helpers never inspect or print `~/.aws`.

## State bootstrap

`infra/bootstrap` creates one private, versioned, SSE-S3 state bucket with public access blocked and `prevent_destroy`. It intentionally uses local state for this one-time bootstrap.

```bash
toolbox run --container aws terraform -chdir=infra/bootstrap init
toolbox run --container aws terraform -chdir=infra/bootstrap apply -var account_id=<12-digit-account-id>
```

Record the `state_bucket` output in a protected GitHub variable. Production uses key `battlevive-bot/production.tfstate`, native S3 lockfile locking, and no DynamoDB table:

```bash
toolbox run --container aws terraform -chdir=infra/production init \
  -backend-config="bucket=<state-bucket>"
```

The plan role can read only the state object. Plan and apply can acquire/release the exact `.tflock` object; only apply can update state. Neither role can delete the state object.

## Terraform CI contract

`infra/tools/validate.sh` is the single CI-safe static gate: Terraform format and validation, recursive TFLint, Trivy HIGH/CRITICAL configuration scanning, ShellCheck, JSON validation, and Bash parsing. A pull request assumes the plan role through the protected `infrastructure-plan` environment and runs validation followed by `TF_STATE_BUCKET=<bucket> infra/tools/terraform-ci.sh plan`. The plan role is read-only apart from the lockfile. A protected main-branch apply job assumes the apply role through `infrastructure-apply`, regenerates the reviewed plan with the same command, obtains approval, and runs `terraform-ci.sh apply` against that exact saved plan. Never pass secret values as Terraform variables.

The first manual adoption precedes CI activation. Until an authenticated replacement-free plan and empty post-apply plan exist, the Terraform configuration is validated code—not evidence that production has been adopted.

## Sanitized inventory and import

Run `infra/tools/inventory.sh` only after SSO authentication. Its JSON allowlist contains EC2 identity/topology, security-group ingress, volume properties, and SSM registration. It never requests tags beyond the selector, user data, console output, commands, Parameter Store values, auth state, or file contents. Obtain filesystem/free-space and Docker/Compose/deployment-path facts during the interactive SSM session without printing environment variables or files.

Copy sanitized values into an ignored `infra/production/terraform.tfvars`, using `terraform.tfvars.example` as the schema. Set `create_imports=true` only for the first manual adoption plan. The import blocks cover the existing EC2 instance, security group, IAM role, and instance profile; the instance resource adopts root-volume settings.

```bash
toolbox run --container aws terraform -chdir=infra/production plan -out=adoption.tfplan
toolbox run --container aws terraform -chdir=infra/production show adoption.tfplan
```

Stop if the plan proposes any replacement or destroy. The permitted adoption plan contains imports and in-place hardening only. Confirm the AMI, Availability Zone, root device, volume size/type/encryption, profile, and subnet against inventory before apply. After the manual apply, set `create_imports=false` and require an empty plan. Shared/default VPC and subnet are data sources.

The EC2 instance, security group, instance profile/role, root EBS attachment, state bucket, and operations bucket use deletion protections where supported. EC2 termination protection is enabled and root `delete_on_termination` is false.

If the existing EBS volume is unencrypted, do not apply the encrypted flag in place. Stop the instance, snapshot it, create an encrypted snapshot copy and replacement volume with the AWS-managed EBS key, validate the new volume, and retain the original rollback volume through acceptance. This is a separately approved maintenance operation.

## SSM-only administration transition

Use this exact order:

1. Attach/verify the Terraform-managed instance profile and a healthy SSM Agent.
2. Keep the current administrative session open and start a second real session with `infra/tools/ssm-session.sh`.
3. Verify the `battlevive-production-shell` document streams to the 90-day Session Manager log group.
4. Apply the zero-ingress security group, confirming every ingress rule—including port 22—is absent.
5. Disable SSH with `sudo systemctl disable --now sshd` inside SSM.
6. Start a third, new SSM session before closing either previous session.

Session Manager needs no inbound port or SSH keys. Never remove ingress or stop `sshd` until a real new SSM session succeeds.

## Secrets and host installation

Terraform records Parameter names only; it never creates secret values. Run `infra/tools/migrate-secrets.sh` interactively. The helper uses hidden input and root-private temporary JSON sent via `--cli-input-json`; values do not appear in process arguments or output. On EC2, `render-secrets` atomically writes root-owned, runtime-group `0640` files below root:10001 mode `0750` `/run/battlevive`, including `postgres-password` for PostgreSQL's supported password-file input. The bot runs as UID/GID 10001 and can read but not replace those files. Root-only mode `0600` `host.env` contains non-secret AWS region, operations bucket, log group, absolute data/deployment paths, and PostgreSQL names; production does not use `.env`.

The release bundle root is `/opt/battlevive/current`, with `compose.yaml`, `compose.aws.yaml`, `install.sh`, and `scripts/`. Its root-only installer copies the sole deploy executable to `/usr/local/libexec/battlevive/deploy`, creates UID/GID 10001 bot data and isolated PostgreSQL data directories, and establishes the CloudWatch system source with rsyslog at `/var/log/battlevive-system.log`. It also installs secret rendering, weekly backups, monthly restore verification, daily freshness checks, minute health publishing, and systemd timers. Application logs remain Docker stdout/stderr; `BATTLEVIVE_LOG_GROUP=/battlevive/production/application` selects the provisioned and authorized 90-day group.

The deploy transport is only the `battlevive-production-deploy` SSM document. It injects the Terraform-provisioned operations bucket/region and invokes the canonical executable. GitHub AWS jobs use the protected `production` environment; the deploy role can read only atomic `/battlevive/production/deployment/state`, upload release objects, and send that one document to the tagged instance.

## Backups and recovery

`infra/host/scripts/backup.sh weekly` and synchronous `backup.sh --type pre-deploy --verify` create PostgreSQL custom-format archives. Each run verifies `pg_restore --list`, writes a SHA-256 sidecar and JSON manifest (UTC time, application version, schema version, key-table counts), uploads a unique key, and publishes success/failure metrics. Weekly objects expire after 84 days; predeploy objects expire after 30 days. Incomplete multipart uploads abort after seven days. Scheduled backup, restore drill, and deployment use `/run/lock/battlevive-operations.lock`; deployment holds it across backup, migration, and health transition.

Monthly `restore-verify.sh` takes the shared operations lock, downloads the newest weekly archive and sidecars, verifies SHA-256, restores into a uniquely named temporary database, compares key-table counts, records a drill marker, and always drops the temporary database. Alarms cover failed and stale backups/drills.

Recovery target is seven-day RPO and two-hour RTO:

1. Open a new SSM session and stop only the bot; keep PostgreSQL available if it is healthy.
2. Select the newest trusted weekly or predeploy S3 prefix. Download all three files and run `sha256sum --check` plus `pg_restore --list`.
3. Restore first into an isolated database and compare its manifest counts/schema version.
4. Take a final snapshot of the failed database, stop Compose, and replace the database only after isolated validation.
5. Run migrations from the intended immutable application bundle, start database/migration/bot in dependency order, and verify database plus bot health for 15 minutes.
6. If application health fails, restore the prior bundle and image digest. Migrations are backward-compatible with the previous image; never attempt an automatic database downgrade.

## Monitoring and resize gate

After live installation, CloudWatch is expected to collect memory, swap, instance-aggregated disk/inodes, processes, EC2 status/CPU credits, bot/telemetry heartbeats, backup/restore/deployment metrics, rsyslog system logs, application critical logs, and OOM events. Terraform provisions corresponding SNS alarms, including explicit deployment failure. Confirm streams and alarm delivery during live acceptance; configuration validation alone does not prove telemetry delivery.

Run an image-render/database workload, then collect 168 continuous hours after monitoring installation. `infra/tools/resize-gate.sh` requires at least 98% of expected datapoints with no gap or edge gap above two periods for every required series. It fails closed unless working memory is at most 300 MiB, average swap is zero/no spike exceeds 64 MiB, disk and inode use stay below 70%, credits are adequate with no surplus charge, status/OOM failures remain zero, and bot plus host-telemetry health remain continuous. If any criterion fails, retain `t4g.micro`.

For an eligible resize, first require a Terraform plan containing only `t4g.micro -> t4g.nano`, take a fresh backup and EBS snapshot, apply during a maintenance window, and validate SSM, database, bot health, logs, memory, swap, and Discord behavior for 15 minutes. A non-Elastic public IPv4 can change on stop/start. Immediately restore `t4g.micro` through Terraform if a gate fails.
