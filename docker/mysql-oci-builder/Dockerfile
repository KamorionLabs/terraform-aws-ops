FROM oraclelinux:9-slim

LABEL org.opencontainers.image.title="mysql-oci-builder"
LABEL org.opencontainers.image.description="Refresh archive builder: S3 dump/media archive and/or OCI data-image build (buildah -> ECR)"
LABEL org.opencontainers.image.source="https://github.com/KamorionLabs/terraform-aws-ops"
LABEL org.opencontainers.image.vendor="KamorionLabs"

ARG TARGETARCH

# MySQL 8.4 LTS community client (S3 archive path), buildah (OCI build path, daemonless),
# awscli (ECR login / S3 sync), plus tar/gzip/jq utilities.
RUN set -eux; \
    { \
        echo '[mysql84-community]'; \
        echo 'name=MySQL 8.4 LTS Community Server'; \
        echo 'baseurl=https://repo.mysql.com/yum/mysql-8.4-community/el/9/$basearch/'; \
        echo 'enabled=1'; \
        echo 'gpgcheck=1'; \
        echo 'gpgkey=https://repo.mysql.com/RPM-GPG-KEY-mysql-2023 https://repo.mysql.com/RPM-GPG-KEY-mysql-2025'; \
    } > /etc/yum.repos.d/mysql-community.repo; \
    microdnf install -y \
        mysql-community-client \
        buildah \
        bash \
        curl \
        unzip \
        gzip \
        tar \
        jq; \
    case "${TARGETARCH}" in \
        arm64) AWSCLI_ARCH="aarch64" ;; \
        *)     AWSCLI_ARCH="x86_64"  ;; \
    esac; \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWSCLI_ARCH}.zip" -o /tmp/awscliv2.zip; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/aws /tmp/awscliv2.zip; \
    microdnf remove -y unzip; \
    microdnf clean all; \
    rm -rf /var/cache/dnf

# buildah uses the vfs storage driver (no fuse / no host mounts required).
ENV STORAGE_DRIVER=vfs \
    BUILDAH_ISOLATION=chroot

COPY archive.sh /archive.sh
RUN chmod +x /archive.sh

ENTRYPOINT ["/archive.sh"]
