# mysql-oci-builder

End-of-refresh archive builder image used by the `run_archive_job` Step Function. From the
same refreshed dataset it produces, toggle-driven, either or both of:

| Toggle (`EKS.ArchiveJob`) | Output | Consumed by |
|---------------------------|--------|-------------|
| `S3Sync` (default `true`) | gzip SQL dump + media tar, `aws s3 sync` to S3 | local dev docker tooling (legacy) |
| `OciBuild` (default `false`) | OCI data image (MySQL datadir baked), pushed to ECR | dev `docker pull`, ephemeral EKS envs |

Both toggles are independent: a single archive job can emit the S3 archive, the OCI image, or both.
Daemonless build via **buildah** (vfs storage driver) — no Docker daemon, runs in an EKS Job.

Generic image, published multi-arch (amd64/arm64) to `ghcr.io/kamorionlabs/mysql-oci-builder` and
Docker Hub by `.github/workflows/docker-mysql-oci-builder.yml` (same pattern as `mysql-s3`). Consumers
set it as the archive job `JobImage`. The **produced data images** are pushed to a destination-owned
ECR repo (`OCI_ECR_REPO`), never baked into this generic image.

## How the OCI image is built

1. `buildah from $OCI_BASE_IMAGE` (a MySQL image of `$OCI_MYSQL_VERSION`).
2. Inside that rootfs (`buildah run`, dump bind-mounted read-only from `/refresh`): `mysqld
   --initialize-insecure`, start on a local socket, import every `/refresh/*.sql` into
   `$MYSQL_DATABASE`, clean `mysqladmin shutdown` → consistent datadir.
3. `buildah config` (entrypoint `mysqld`, labels) → `buildah commit` → `buildah push` to
   `$OCI_ECR_REPO:$OCI_IMAGE_TAG-YYYYMMDD` and the moving `:$OCI_IMAGE_TAG`.
4. Optional read-only media artifact pushed to `$OCI_MEDIA_REPO` when `OCI_MEDIA=true`.

## Hard requirements / gotchas

- **Datadir outside the VOLUME**: the datadir is baked at `OCI_DATADIR` (default `/data/mysql`),
  deliberately OUTSIDE the base image's `VOLUME` (usually `/var/lib/mysql`) — a `VOLUME` path is not
  captured by `buildah commit`. This means **any MySQL image works as base, no no-VOLUME rebuild**.
  The entrypoint fails fast if `OCI_DATADIR` happens to sit under a declared VOLUME.
- **Version parity**: the datadir format is bound to the mysqld version. `OCI_BASE_IMAGE` must be
  the same MySQL version devs/EKS run (no cross-minor downgrade of a datadir).
- **securityContext**: buildah needs a privileged (or fuse-overlayfs) pod. `run_archive_job`
  injects `{privileged:true}` when `OciBuild=true` (overridable via `ArchiveJob.Oci.SecurityContext`).
- **Scratch space**: the build writes the datadir under the node-ephemeral `scratch` emptyDir —
  the node must have enough ephemeral storage for the dataset. Not suitable for very large DBs
  (TB-scale datasets) — keep those on snapshot/clone paths.

## Env contract

Always: `MYSQL_HOST MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE SHARED_FOLDER SHARED_MEDIA_FOLDER SCRATCH_DIR`
S3: `S3_SYNC S3_BUCKET S3_PREFIX MEDIAFOLDER`
OCI: `OCI_BUILD OCI_ECR_REPO OCI_IMAGE_TAG OCI_BASE_IMAGE OCI_MYSQL_VERSION OCI_DATADIR AWS_REGION OCI_MEDIA OCI_MEDIA_REPO`

All values are wired by `modules/step-functions/utils/run_archive_job.asl.json` (state
`BuildArchiveEnv`) from the refresh input. The job's ServiceAccount needs ECR push (Pod Identity)
when `OciBuild=true`.

## Status

PoC-grade: the S3 archive layout must be validated against what the local dev docker tooling
expects, and the OCI build flow validated end-to-end on a small environment before wider rollout.
See the sibling spec `specs/repl-s3-sync.md`.
