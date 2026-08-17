# Battlevive production infrastructure

Terraform adopts the existing `eu-north-1` EC2 host and adds release operations around it. It does not contain live IDs, ARNs, email addresses, secret values, or Terraform variables generated from secrets.

## User prerequisites

Before any live action:

1. Enable IAM Identity Center, create a temporary bootstrap permission set capable of creating the final scoped roles, and configure the `battlevive-prod` CLI profile: [AWS IAM Identity Center CLI setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).
2. Prepare a Docker Hub read/write automation token: [Docker access tokens](https://docs.docker.com/security/access-tokens/).
3. Choose the operations alert email. Terraform creates an SNS subscription; immediately confirm the AWS email: [SNS email confirmation](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html).
4. Plan to configure GitHub variables, secrets, and protected environments after Terraform outputs the role ARNs: [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

Run `infra/tools/bootstrap-toolbox.sh` to create the `aws` Toolbox and install AWS CLI v2, the Session Manager plugin, Terraform, `jq`, ShellCheck, and pytest. Authenticate explicitly with `aws sso login --profile battlevive-prod`; helpers never inspect or print `~/.aws`.

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

The plan/apply roles can list only the state prefix, read/write the state object, and read/write/delete only the `.tflock` object. They cannot delete the state object.

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

Terraform records Parameter names only; it never creates secret values. Run `infra/tools/migrate-secrets.sh` interactively. The helper uses hidden input and root-private temporary JSON sent via `--cli-input-json`; values do not appear in process arguments or output. On EC2, `render-secrets` atomically writes mode `0600` files below root-owned `/run/battlevive`, including `postgres-password` for PostgreSQL's supported password-file input. `host.env` is root-owned mode `0600` and contains only `POSTGRES_USER` and `POSTGRES_DB`.

The release bundle carries `infra/host`. Run its root-only `install.sh` through the bootstrap document after placement. It installs secret rendering, weekly backups, monthly restore verification, daily freshness checks, minute health publishing, and systemd timers. Application logs remain Docker stdout/stderr; the AWS Compose override sends them to the 90-day application log group.

## Backups and recovery

`infra/host/scripts/backup.sh weekly` and synchronous `backup.sh predeploy` create PostgreSQL custom-format archives. Each run verifies `pg_restore --list`, writes a SHA-256 sidecar and JSON manifest (UTC time, application version, schema version, key-table counts), uploads a unique key, and publishes success/failure metrics. Weekly objects expire after 84 days; predeploy objects expire after 30 days. Incomplete multipart uploads abort after seven days.

Monthly `restore-verify.sh` takes the shared operations lock, downloads the newest weekly archive and sidecars, verifies SHA-256, restores into a uniquely named temporary database, compares key-table counts, records a drill marker, and always drops the temporary database. Alarms cover failed and stale backups/drills.

Recovery target is seven-day RPO and two-hour RTO:

1. Open a new SSM session and stop only the bot; keep PostgreSQL available if it is healthy.
2. Select the newest trusted weekly or predeploy S3 prefix. Download all three files and run `sha256sum --check` plus `pg_restore --list`.
3. Restore first into an isolated database and compare its manifest counts/schema version.
4. Take a final snapshot of the failed database, stop Compose, and replace the database only after isolated validation.
5. Run migrations from the intended immutable application bundle, start database/migration/bot in dependency order, and verify database plus bot health for 15 minutes.
6. If application health fails, restore the prior bundle and image digest. Migrations are backward-compatible with the previous image; never attempt an automatic database downgrade.

## Monitoring and resize gate

CloudWatch collects memory, swap, disk/inodes, processes, EC2 status/CPU credits, bot heartbeat, backup/restore/deployment metrics, system logs, application critical logs, and OOM events. SNS alarms cover the plan’s health, capacity, freshness, and failure conditions.

Run an image-render/database workload, then collect 168 continuous hours after monitoring installation. `infra/tools/resize-gate.sh` fails closed on missing data and requires working memory at most 300 MiB, average swap zero/no spike above 64 MiB, filesystem below 70%, adequate credits/no surplus charge, no status failures, and continuous bot health. Also inspect OOM and deployment alarms. If any criterion fails, retain `t4g.micro`.

For an eligible resize, first require a Terraform plan containing only `t4g.micro -> t4g.nano`, take a fresh backup and EBS snapshot, apply during a maintenance window, and validate SSM, database, bot health, logs, memory, swap, and Discord behavior for 15 minutes. A non-Elastic public IPv4 can change on stop/start. Immediately restore `t4g.micro` through Terraform if a gate fails.
