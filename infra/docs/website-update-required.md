# Canonical website documentation update

The canonical website repository is outside this worktree's writable root, so this branch cannot safely edit it. Update the existing pages—do not add duplicate long-form pages—as follows:

- `content/docs/deployment.mdx`: replace `latest`/manual-SQL production guidance with stable GitHub Release SemVer tags and immutable digest deployment; describe SSM-only access, zero ingress, separate `/var/lib/battlevive/bot` and `/var/lib/battlevive/postgresql` paths, migration/health gates, automatic rollback, stdout/CloudWatch logging, verified S3 backups, and recovery. Remove the false `127.0.0.1:5432` production-binding statement; PostgreSQL is reachable only over the internal Compose network and is not host-published.
- `content/docs/configuration.mdx`: document `*_FILE` precedence/conflict failure; `BATTLEVIVE_TOKEN_STORE=file|ssm`, token SSM parameter and region; local file default; production Parameter Store paths; root-only `/run/battlevive` rendering; and PostgreSQL's `postgres-password` file without duplicating its value.
- `content/docs/database.mdx`: add `schema_migrations(version, filename, sha256, applied_at)`, advisory-locked transactional numbered migrations, checksum mismatch refusal, existing-database adoption replay, backward compatibility with the previous image, weekly/predeploy retention, monthly isolated restore validation, seven-day RPO, and two-hour RTO.
- `content/docs/development.mdx`: replace main-push publication with stable-release tag selection, exact-tag quality suite, multi-platform digest/SBOM/provenance, OIDC deploy contract, infrastructure validation, and the prohibition on live-service probes in normal pytest.
- `content/docs/troubleshooting.mdx`: replace `.env`/token-file-only production advice with local-versus-AWS branches; add stale heartbeat, migration checksum failure, SSM, secret rendering, CloudWatch, backup/restore, and rollback checks. Remove advice to inspect `app/logs` in production.

Validate the website repository with:

```bash
toolbox run --container bloated-jabbasript pnpm exec eslint .
toolbox run --container bloated-jabbasript pnpm exec tsc --noEmit
toolbox run --container bloated-jabbasript pnpm build
```
