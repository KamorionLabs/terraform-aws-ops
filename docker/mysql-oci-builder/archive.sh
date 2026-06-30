#!/bin/bash
set -euo pipefail

# archive.sh - End-of-refresh archive builder.
#
# Two independent, toggle-driven outputs from the same refreshed dataset
# (SQL dump staged on /refresh by run_mysqldump_on_eks + media on /shared-data-media):
#
#   S3_SYNC=true   -> legacy dev archive: gzip the SQL dump + tar the media, push to S3
#                     (consumed by the local dev docker tooling)
#   OCI_BUILD=true -> OCI data image: import the dump into a fresh MySQL datadir baked
#                     on top of OCI_BASE_IMAGE, push to ECR (consumed by dev `docker pull`
#                     and by ephemeral EKS envs). NO Docker daemon: buildah (vfs driver).
#
# Both can run in the same job. Either can be disabled. Toggles + config come from the
# refresh input (EKS.ArchiveJob) via the run_archive_job Step Function.
#
# Env (always):   MYSQL_HOST MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE
#                 SHARED_FOLDER(/refresh) SHARED_MEDIA_FOLDER(/shared-data-media) SCRATCH_DIR(/scratch)
# Env (S3):       S3_SYNC S3_BUCKET S3_PREFIX MEDIAFOLDER
# Env (OCI):      OCI_BUILD OCI_ECR_REPO OCI_IMAGE_TAG OCI_BASE_IMAGE OCI_MYSQL_VERSION
#                 AWS_REGION OCI_MEDIA OCI_MEDIA_REPO

SHARED_FOLDER="${SHARED_FOLDER:-/refresh}"
SHARED_MEDIA_FOLDER="${SHARED_MEDIA_FOLDER:-/shared-data-media}"
SCRATCH_DIR="${SCRATCH_DIR:-/scratch}"
S3_SYNC="${S3_SYNC:-false}"
OCI_BUILD="${OCI_BUILD:-false}"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

require() { for v in "$@"; do [[ -n "${!v:-}" ]] || die "missing required env $v"; done; }

# ---------------------------------------------------------------------------
# S3 archive (legacy dev artifact) — TODO: validate the exact layout expected by
# the local dev docker tooling's DB-restore workflow before decommissioning the
# existing archive image.
# ---------------------------------------------------------------------------
archive_to_s3() {
  require S3_BUCKET S3_PREFIX
  log "S3 archive: gzip SQL dump in ${SHARED_FOLDER}"
  shopt -s nullglob
  for f in "${SHARED_FOLDER}"/*.sql; do
    gzip -f "$f"
  done
  if [[ -n "${MEDIAFOLDER:-}" && -d "${SHARED_MEDIA_FOLDER}/${MEDIAFOLDER}" ]]; then
    log "S3 archive: tar media ${SHARED_MEDIA_FOLDER}/${MEDIAFOLDER}"
    tar -C "${SHARED_MEDIA_FOLDER}" -czf "${SHARED_FOLDER}/media.tar.gz" "${MEDIAFOLDER}"
  fi
  local uri="s3://${S3_BUCKET}/${S3_PREFIX%/}/"
  log "S3 archive: sync ${SHARED_FOLDER}/ -> ${uri}"
  aws s3 sync "${SHARED_FOLDER}/" "${uri}"
  log "S3 archive: done"
}

# ---------------------------------------------------------------------------
# OCI data image: bake an initialized MySQL datadir on top of OCI_BASE_IMAGE.
# OCI_BASE_IMAGE MUST be a MySQL image of version OCI_MYSQL_VERSION WITHOUT a
# `VOLUME /var/lib/mysql` declaration (else buildah commit drops the baked data).
# ---------------------------------------------------------------------------
build_oci_image() {
  require OCI_ECR_REPO OCI_IMAGE_TAG OCI_BASE_IMAGE MYSQL_DATABASE AWS_REGION
  local registry="${OCI_ECR_REPO%%/*}"
  local stamp tag_dated tag_market
  stamp="$(date '+%Y%m%d')"
  tag_dated="${OCI_ECR_REPO}:${OCI_IMAGE_TAG}-${stamp}"
  tag_market="${OCI_ECR_REPO}:${OCI_IMAGE_TAG}"

  log "OCI: ECR login to ${registry}"
  aws ecr get-login-password --region "${AWS_REGION}" \
    | buildah login --username AWS --password-stdin "${registry}"

  log "OCI: buildah from ${OCI_BASE_IMAGE}"
  local ctr
  ctr="$(buildah from "${OCI_BASE_IMAGE}")"
  trap 'buildah rm "${ctr}" >/dev/null 2>&1 || true' EXIT

  # Guard: a VOLUME on the datadir would void the committed data.
  if buildah inspect --format '{{json .Docker.Config.Volumes}}' "${ctr}" 2>/dev/null \
       | grep -q '/var/lib/mysql'; then
    die "OCI_BASE_IMAGE declares VOLUME /var/lib/mysql — use a no-VOLUME variant (data would be dropped on commit)"
  fi

  log "OCI: initialize + import dump into the base image datadir (mysqld ${OCI_MYSQL_VERSION:-?})"
  buildah run \
    --volume "${SHARED_FOLDER}:/refresh:ro" \
    --env "MYSQL_DATABASE=${MYSQL_DATABASE}" \
    "${ctr}" -- bash -euo pipefail -c '
      datadir=/var/lib/mysql
      sock=/tmp/oci-build.sock
      rm -rf "${datadir:?}/"* || true
      mysqld --initialize-insecure --datadir="$datadir" --user=root
      mysqld --datadir="$datadir" --user=root --skip-networking --socket="$sock" &
      pid=$!
      for i in $(seq 1 60); do mysqladmin --socket="$sock" ping >/dev/null 2>&1 && break; sleep 1; done
      mysql --socket="$sock" -e "CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\`;"
      for f in /refresh/*.sql; do
        echo "importing $(basename "$f")"
        mysql --socket="$sock" "${MYSQL_DATABASE}" < "$f"
      done
      mysqladmin --socket="$sock" shutdown
      wait "$pid" 2>/dev/null || true
    '

  log "OCI: configure image metadata (no VOLUME; mysqld on baked datadir)"
  buildah config \
    --entrypoint '["mysqld","--datadir=/var/lib/mysql"]' \
    --cmd '' \
    --label "org.kamorion.refresh.market=${OCI_IMAGE_TAG}" \
    --label "org.kamorion.refresh.date=${stamp}" \
    --label "org.kamorion.refresh.mysql=${OCI_MYSQL_VERSION:-unknown}" \
    "${ctr}"

  log "OCI: commit + push ${tag_dated} and ${tag_market}"
  buildah commit "${ctr}" "${tag_dated}"
  buildah tag "${tag_dated}" "${tag_market}"
  buildah push "${tag_dated}"
  buildah push "${tag_market}"
  log "OCI: data image pushed"

  if [[ "${OCI_MEDIA:-false}" == "true" && -n "${OCI_MEDIA_REPO:-}" && -n "${MEDIAFOLDER:-}" ]]; then
    log "OCI: build read-only media artifact -> ${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}-${stamp}"
    local mctr
    mctr="$(buildah from scratch)"
    buildah copy "${mctr}" "${SHARED_MEDIA_FOLDER}/${MEDIAFOLDER}" /media
    buildah commit "${mctr}" "${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}-${stamp}"
    buildah tag "${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}-${stamp}" "${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}"
    buildah push "${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}-${stamp}"
    buildah push "${OCI_MEDIA_REPO}:${OCI_IMAGE_TAG}"
    buildah rm "${mctr}" >/dev/null 2>&1 || true
    log "OCI: media artifact pushed"
  fi
}

# ---------------------------------------------------------------------------
main() {
  log "archive.sh start (S3_SYNC=${S3_SYNC} OCI_BUILD=${OCI_BUILD})"
  if [[ "${S3_SYNC}" == "true" ]]; then archive_to_s3; else log "S3 archive skipped"; fi
  if [[ "${OCI_BUILD}" == "true" ]]; then build_oci_image; else log "OCI build skipped"; fi
  log "archive.sh done"
}
main "$@"
