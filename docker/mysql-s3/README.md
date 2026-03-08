# mysql-s3

Lightweight Docker images for MySQL/MariaDB dump and import with S3 sync support. Designed for Kubernetes jobs in database refresh pipelines.

## Images

| Tag | Base | MySQL Client | Size |
|-----|------|-------------|------|
| `latest`, `mariadb` | Alpine 3.23 | MariaDB (compatible MySQL 5.7/8.0) | ~50MB |
| `mysql8` | Oracle Linux 9 slim | MySQL 8.4 LTS (official) | ~200MB |

Both variants include: `mysql`, `mysqldump`, `aws` CLI, `bash`.

**Multi-arch**: `linux/amd64`, `linux/arm64`

## When to use which variant?

- **mariadb** (default): Best for **imports** (`mysql` command). Lightweight, fast to pull. MariaDB client is wire-compatible with MySQL 8.0 for all import operations.
- **mysql8**: Required for **dumps** using MySQL-specific options like `--set-gtid-purged=OFF` (Aurora MySQL). Also needed if you rely on MySQL 8.x-specific client features.

## Quick Start

### Import SQL files from S3

```bash
docker run --rm \
  -e MYSQL_HOST=my-db.cluster-xxx.rds.amazonaws.com \
  -e MYSQL_USER=admin \
  -e MYSQL_PASSWORD='my-pa$$word' \
  -e MYSQL_DATABASE=mydb \
  -e S3_SYNC=true \
  -e S3_BUCKET=my-bucket \
  -e S3_PREFIX=sql-scripts/mydb \
  kamorion/mysql-s3:latest
```

### Dump a database to S3

```bash
docker run --rm \
  --entrypoint /dump.sh \
  -e MYSQL_HOST=my-db.cluster-xxx.rds.amazonaws.com \
  -e MYSQL_USER=admin \
  -e MYSQL_PASSWORD='my-pa$$word' \
  -e MYSQL_DATABASE=mydb \
  -e S3_SYNC=true \
  -e S3_BUCKET=my-bucket \
  -e S3_BUCKET_PREFIX=dumps/mydb \
  kamorion/mysql-s3:latest
```

## Environment Variables

### import.sh (default entrypoint)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MYSQL_HOST` | Yes | - | MySQL server hostname |
| `MYSQL_USER` | Yes | - | MySQL username |
| `MYSQL_PASSWORD` | Yes | - | MySQL password (special chars safe) |
| `MYSQL_DATABASE` | No | - | Target database (if unset, SQL files must contain USE statements) |
| `MYSQL_PORT` | No | `3306` | MySQL port |
| `S3_SYNC` | No | - | Set to any value to sync from S3 before import |
| `S3_BUCKET` | If S3_SYNC | - | S3 bucket name |
| `S3_PREFIX` | No | - | S3 key prefix |
| `LOCAL_DIR` | No | `/tmp/s3_local` | Local directory for SQL files |
| `CONTINUE_ON_ERROR` | No | - | Set to any value to continue on SQL errors |

### dump.sh

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MYSQL_HOST` | Yes | - | MySQL server hostname |
| `MYSQL_USER` | Yes | - | MySQL username |
| `MYSQL_PASSWORD` | Yes | - | MySQL password (special chars safe) |
| `MYSQL_DATABASE` | Cond. | - | Database to dump (required unless ALL_DATABASES) |
| `MYSQL_TABLE` | No | - | Single table to dump |
| `MYSQL_PORT` | No | `3306` | MySQL port |
| `ALL_DATABASES` | No | - | Set to dump all user databases |
| `IGNORE_DATABASE` | No | - | Database to skip (with ALL_DATABASES) |
| `MYSQLDUMP_OPTS` | No | `--set-gtid-purged=OFF` | Extra mysqldump options |
| `S3_SYNC` | No | - | Set to sync dump files to S3 |
| `S3_BUCKET` | If S3_SYNC | - | S3 bucket name |
| `S3_BUCKET_PREFIX` | No | - | S3 key prefix for upload |
| `LOCAL_DIR` | No | `/tmp/s3_local` | Local directory for dump files |

## Password Handling

Passwords with special characters (`$`, `#`, `*`, `!`, etc.) are handled safely using MySQL's `--defaults-extra-file` mechanism. The password is written to a temporary config file (chmod 600) and cleaned up on exit. No shell quoting issues.

## Kubernetes Job Example

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-import
spec:
  template:
    spec:
      serviceAccountName: refresh
      containers:
        - name: import
          image: kamorion/mysql-s3:latest
          env:
            - name: MYSQL_HOST
              value: "my-db.cluster-xxx.rds.amazonaws.com"
            - name: MYSQL_USER
              value: "admin"
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: db-credentials
                  key: password
            - name: MYSQL_DATABASE
              value: "mydb"
            - name: S3_SYNC
              value: "true"
            - name: S3_BUCKET
              value: "my-bucket"
            - name: S3_PREFIX
              value: "sql-scripts/mydb"
      restartPolicy: Never
  backoffLimit: 1
```

## Building Locally

```bash
# MariaDB variant
docker build -t mysql-s3:mariadb docker/mysql-s3/

# MySQL 8 variant
docker build -t mysql-s3:mysql8 -f docker/mysql-s3/Dockerfile.mysql8 docker/mysql-s3/
```

## License

Apache 2.0 - [KamorionLabs](https://github.com/KamorionLabs)
