FROM alpine:3.23

LABEL org.opencontainers.image.title="mysql-s3"
LABEL org.opencontainers.image.description="MySQL dump/import with S3 sync support"
LABEL org.opencontainers.image.source="https://github.com/KamorionLabs/terraform-aws-ops"
LABEL org.opencontainers.image.vendor="KamorionLabs"

RUN apk add --no-cache \
    bash \
    aws-cli \
    mysql-client

COPY import.sh /import.sh
COPY dump.sh /dump.sh
RUN chmod +x /import.sh /dump.sh

ENTRYPOINT ["/import.sh"]
