#!/usr/bin/env python3
"""
Generate JSON input files for refresh Step Functions.

Usage:
    python generate_refresh_inputs.py --scenario mono-account --output-dir ./test-mi3
    python generate_refresh_inputs.py --scenario cross-account-it --output-dir ./test-cross-it
    python generate_refresh_inputs.py --source-cluster my-prod --dest-cluster my-staging \
        --source-profile prod --dest-profile staging --output-dir ./test-custom
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class AccountConfig:
    account_id: str
    role_arn: str
    profile: str

    def to_dict(self) -> dict:
        return {
            "AccountId": self.account_id,
            "RoleArn": self.role_arn
        }


@dataclass
class ClusterInfo:
    identifier: str
    engine: Optional[str] = None
    engine_version: Optional[str] = None
    security_groups: list = field(default_factory=list)
    subnet_group: Optional[str] = None
    parameter_group: Optional[str] = None
    instance_parameter_group: Optional[str] = None
    kms_key_id: Optional[str] = None
    tags: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class RefreshConfig:
    source: AccountConfig
    destination: AccountConfig
    source_cluster: ClusterInfo
    dest_cluster: ClusterInfo
    tmp_cluster_identifier: str
    region: str
    orchestrator_account: str = "025922408720"
    orchestrator_profile: str = "shared-services/AWSAdministratorAccess"
    sfn_prefix: str = "sfn-dig-tooling-refresh"

    @property
    def is_same_account(self) -> bool:
        return self.source.account_id == self.destination.account_id

    @property
    def prepare_sfn_arn(self) -> str:
        return f"arn:aws:states:{self.region}:{self.orchestrator_account}:stateMachine:{self.sfn_prefix}-db-prepare-snapshot-for-restore"


class AWSClient:
    """Wrapper for AWS CLI calls."""

    @staticmethod
    def run(cmd: list, profile: str, region: str) -> Optional[dict]:
        full_cmd = ["aws"] + cmd + ["--profile", profile, "--region", region, "--output", "json"]
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
            return json.loads(result.stdout) if result.stdout.strip() else None
        except subprocess.CalledProcessError as e:
            print(f"  Warning: {' '.join(cmd[:3])}... failed: {e.stderr.strip()}", file=sys.stderr)
            return None
        except json.JSONDecodeError:
            return None

    @classmethod
    def get_account_id(cls, profile: str) -> Optional[str]:
        result = cls.run(["sts", "get-caller-identity"], profile, "eu-central-1")
        return result.get("Account") if result else None

    @classmethod
    def get_cluster_info(cls, cluster_id: str, profile: str, region: str) -> Optional[ClusterInfo]:
        result = cls.run([
            "rds", "describe-db-clusters",
            "--db-cluster-identifier", cluster_id
        ], profile, region)

        if not result or not result.get("DBClusters"):
            return None

        cluster = result["DBClusters"][0]

        # Get instance parameter group from first instance if available
        instance_pg = None
        instances = cluster.get("DBClusterMembers", [])
        if instances:
            instance_id = instances[0].get("DBInstanceIdentifier")
            if instance_id:
                instance_result = cls.run([
                    "rds", "describe-db-instances",
                    "--db-instance-identifier", instance_id
                ], profile, region)
                if instance_result and instance_result.get("DBInstances"):
                    pgs = instance_result["DBInstances"][0].get("DBParameterGroups", [])
                    if pgs:
                        instance_pg = pgs[0].get("DBParameterGroupName")

        return ClusterInfo(
            identifier=cluster_id,
            engine=cluster.get("Engine"),
            engine_version=cluster.get("EngineVersion"),
            security_groups=[sg["VpcSecurityGroupId"] for sg in cluster.get("VpcSecurityGroups", [])],
            subnet_group=cluster.get("DBSubnetGroup"),
            parameter_group=cluster.get("DBClusterParameterGroup"),
            instance_parameter_group=instance_pg,
            kms_key_id=cluster.get("KmsKeyId"),
            tags=cluster.get("TagList", [])
        )

    @classmethod
    def get_parameter_groups(cls, profile: str, region: str, family: str = "aurora-mysql8.0") -> List[str]:
        """Get available DB parameter groups for a family."""
        result = cls.run([
            "rds", "describe-db-parameter-groups",
            "--query", f"DBParameterGroups[?DBParameterGroupFamily=='{family}'].DBParameterGroupName"
        ], profile, region)
        return result if isinstance(result, list) else []


class InputGenerator:
    """Generates JSON input files for refresh Step Functions."""

    def __init__(self, config: RefreshConfig, output_dir: Path):
        self.config = config
        self.output_dir = output_dir

    def generate_all(self):
        """Generate all input files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ["phase1-db", "phase1-efs", "phase2-switch", "phase3-cleanup", "scripts"]:
            (self.output_dir / subdir).mkdir(exist_ok=True)

        self._generate_common_variables()
        self._generate_phase1_db()
        self._generate_phase2_switch()
        self._generate_phase3_cleanup()
        self._generate_run_script()

        self._print_summary()

    def _write_json(self, path: Path, data: dict):
        """Write JSON file with pretty formatting."""
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Generated: {path.relative_to(self.output_dir)}")

    def _generate_common_variables(self):
        self._write_json(self.output_dir / "common-variables.json", {
            "_comment": f"Generated on {datetime.now().isoformat()}",
            "SourceAccount": self.config.source.to_dict(),
            "DestinationAccount": self.config.destination.to_dict(),
            "IsSameAccount": self.config.is_same_account,
            "Region": self.config.region,
            "PrepareSnapshotStateMachineArn": self.config.prepare_sfn_arn,
            "TmpClusterIdentifier": self.config.tmp_cluster_identifier
        })

    def _build_restore_tags(self) -> List[Dict[str, str]]:
        """Build tags for restored resources."""
        # Start with source tags, modify for destination
        tags = []
        source_tags = {t["Key"]: t["Value"] for t in self.config.source_cluster.tags}

        # Keep some tags, modify others
        for key, value in source_tags.items():
            if key in ["Name"]:
                tags.append({"Key": key, "Value": self.config.tmp_cluster_identifier})
            elif key in ["nbs_environment", "customer_environment"]:
                # Change prod -> staging for refresh
                if "prod" in value.lower():
                    tags.append({"Key": key, "Value": "staging"})
                else:
                    tags.append({"Key": key, "Value": value})
            elif key not in ["terraform"]:  # Skip terraform tag
                tags.append({"Key": key, "Value": value})

        # Add refresh-specific tags
        tags.append({"Key": "refresh", "Value": "true"})
        tags.append({"Key": "refresh-cluster", "Value": self.config.source_cluster.identifier})

        return tags

    def _generate_phase1_db(self):
        dest = self.config.destination.to_dict()
        src = self.config.source.to_dict()
        dc = self.config.dest_cluster
        tmp_id = self.config.tmp_cluster_identifier

        # 01 - Ensure cluster not exists
        self._write_json(self.output_dir / "phase1-db/01-ensure-cluster-not-exists.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": tmp_id
        })

        # 02 - Restore cluster
        self._write_json(self.output_dir / "phase1-db/02-restore-cluster.json", {
            "SourceAccount": src,
            "DestinationAccount": dest,
            "PrepareSnapshotStateMachineArn": self.config.prepare_sfn_arn,
            "SourceDBClusterIdentifier": self.config.source_cluster.identifier,
            "TmpDbClusterIdentifier": tmp_id,
            "RestoreType": "from-snapshot",
            "SnapshotMode": "copy-latest",
            "DbInstanceClass": "db.serverless",
            "AuroraServerlessMinCapacity": 0.5,
            "AuroraServerlessMaxCapacity": 6,
            "DbSubnetGroupName": dc.subnet_group or "PLACEHOLDER_SUBNET_GROUP",
            "VpcSecurityGroupIds": dc.security_groups or ["PLACEHOLDER_SG"],
            "DbClusterParameterGroupName": dc.parameter_group or "default.aurora-mysql8.0",
            "KmsKeyId": dc.kms_key_id or "alias/aws/rds"
        })

        # 03 - Create instance
        # Use destination instance parameter group, or try to find one, or use default
        instance_pg = dc.instance_parameter_group or "default.aurora-mysql8.0"

        # Build tags for the instance
        restore_tags = self._build_restore_tags()

        self._write_json(self.output_dir / "phase1-db/03-create-instance.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": tmp_id,
            "DbInstanceIdentifierPrefix": tmp_id,
            "DbParameterGroupName": instance_pg,
            "DbSubnetGroupName": dc.subnet_group or "PLACEHOLDER_SUBNET_GROUP",
            "Tags": restore_tags,
            "DbInstances": [
                {
                    "DbInstanceClass": "db.serverless",
                    "PromotionTier": 0
                }
            ]
        })

        # 04 - Enable master secret
        self._write_json(self.output_dir / "phase1-db/04-enable-master-secret.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": tmp_id,
            "MasterUserSecretKmsKeyId": "alias/aws/secretsmanager"
        })

    def _generate_phase2_switch(self):
        dest = self.config.destination.to_dict()
        dc_id = self.config.dest_cluster.identifier
        tmp_id = self.config.tmp_cluster_identifier

        # 01 - Rename old cluster
        self._write_json(self.output_dir / "phase2-switch/01-rename-old-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": dc_id,
            "NewDbClusterIdentifier": f"{dc_id}-old",
            "NewInstanceIdentifierPrefix": f"{dc_id}-old"
        })

        # 02 - Rename new cluster
        self._write_json(self.output_dir / "phase2-switch/02-rename-new-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": tmp_id,
            "NewDbClusterIdentifier": dc_id,
            "NewInstanceIdentifierPrefix": dc_id
        })

    def _generate_phase3_cleanup(self):
        dest = self.config.destination.to_dict()
        dc_id = self.config.dest_cluster.identifier
        today = datetime.now().strftime("%Y%m%d")

        # 01 - Delete old cluster
        self._write_json(self.output_dir / "phase3-cleanup/01-delete-old-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": f"{dc_id}-old",
            "SkipFinalSnapshot": False,
            "FinalDbSnapshotIdentifier": f"{dc_id}-final-{today}"
        })

        # 02 - Stop cluster
        self._write_json(self.output_dir / "phase3-cleanup/02-stop-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": dc_id
        })

    def _generate_run_script(self):
        script = '''#!/bin/bash
set -euo pipefail

SFN_PREFIX="${SFN_PREFIX:-sfn-dig-tooling-refresh}"
AWS_PROFILE="${AWS_PROFILE:-shared-services/AWSAdministratorAccess}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
ORCHESTRATOR_ACCOUNT="025922408720"

usage() {
    echo "Usage: $0 <step-function-suffix> <input-file> [execution-name]"
    echo "Example: $0 db-ensure-cluster-not-exists phase1-db/01-ensure-cluster-not-exists.json"
    echo ""
    echo "Available step functions:"
    echo "  db-ensure-cluster-not-exists"
    echo "  db-restore-cluster"
    echo "  db-create-instance"
    echo "  db-enable-master-secret"
    echo "  db-rename-cluster"
    echo "  db-delete-cluster"
    echo "  db-stop-cluster"
    echo "  efs-restore-from-backup"
    exit 1
}

[[ $# -lt 2 ]] && usage

SFN_SUFFIX="$1"
INPUT_FILE="$2"
EXEC_NAME="${3:-run-$(date +%Y%m%d-%H%M%S)}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT_PATH="$SCRIPT_DIR/../$INPUT_FILE"

[[ ! -f "$INPUT_PATH" ]] && echo "Error: $INPUT_PATH not found" && exit 1

SFN_ARN="arn:aws:states:${AWS_REGION}:${ORCHESTRATOR_ACCOUNT}:stateMachine:${SFN_PREFIX}-${SFN_SUFFIX}"

echo "Step Function: ${SFN_PREFIX}-${SFN_SUFFIX}"
echo "Input: $INPUT_FILE"
echo "Execution: $EXEC_NAME"
echo ""

RESULT=$(aws stepfunctions start-execution \\
    --state-machine-arn "$SFN_ARN" \\
    --name "$EXEC_NAME" \\
    --input "$(cat "$INPUT_PATH")" \\
    --profile "$AWS_PROFILE" \\
    --region "$AWS_REGION")

EXEC_ARN=$(echo "$RESULT" | jq -r '.executionArn')
echo "Started: $EXEC_ARN"
echo ""
echo "Monitor:"
echo "  aws stepfunctions describe-execution --execution-arn '$EXEC_ARN' --query 'status' --output text --profile $AWS_PROFILE --region $AWS_REGION"
'''
        script_path = self.output_dir / "scripts/run-step.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)
        print(f"  Generated: scripts/run-step.sh")

    def _print_summary(self):
        print("\n" + "=" * 60)
        print(f"Generated inputs in: {self.output_dir}")
        print("=" * 60)
        print(f"Source cluster:      {self.config.source_cluster.identifier}")
        print(f"Source account:      {self.config.source.account_id}")
        print(f"Destination cluster: {self.config.dest_cluster.identifier}")
        print(f"Destination account: {self.config.destination.account_id}")
        print(f"Temp cluster:        {self.config.tmp_cluster_identifier}")
        print(f"Mode:                {'Same-account' if self.config.is_same_account else 'Cross-account'}")
        print("\nTo run:")
        print(f"  cd {self.output_dir}")
        print("  ./scripts/run-step.sh db-restore-cluster phase1-db/02-restore-cluster.json")


# =============================================================================
# Presets - Account and role configurations
# =============================================================================

# Role naming patterns per account
ROLE_PATTERNS = {
    # Legacy NBS account (073290922796)
    "073290922796": {
        "source": "rubix-refresh-source-role",
        "destination": "rubix-refresh-destination-role"
    },
    # NewHorizon accounts use iam-dig-{env}-refresh-{source|destination}-role
    "281127105461": {  # staging
        "source": "iam-dig-stg-refresh-source-role",
        "destination": "iam-dig-stg-refresh-destination-role"
    },
    "287223952330": {  # preprod
        "source": "iam-dig-ppd-refresh-source-role",
        "destination": "iam-dig-ppd-refresh-destination-role"
    },
    "366483377530": {  # prod
        "source": "iam-dig-prd-refresh-source-role",
        "destination": "iam-dig-prd-refresh-destination-role"
    },
    "492919832539": {  # integration
        "source": "iam-dig-int-refresh-source-role",
        "destination": "iam-dig-int-refresh-destination-role"
    },
}


def get_role_name(account_id: str, role_type: str) -> str:
    """Get the correct role name for an account."""
    if account_id in ROLE_PATTERNS:
        return ROLE_PATTERNS[account_id][role_type]
    # Default pattern for unknown accounts
    return f"refresh-{role_type}-role"


PRESETS = {
    "mono-account": {
        "description": "MI3 prod -> staging in legacy NBS account",
        "source_profile": "iph",
        "dest_profile": "iph",
        "source_cluster": "mi3-prod-eks-cluster",
        "dest_cluster": "mi3-staging-eks-cluster",
        "tmp_cluster": "mi3-staging-eks-cluster-restore",
        "region": "eu-central-1",
    },
    "cross-account-it": {
        "description": "IT prod (legacy) -> staging (NewHorizon)",
        "source_profile": "iph",
        "dest_profile": "digital-webshop-staging/AWSAdministratorAccess",
        "source_cluster": "it-prod-eks-cluster",
        "dest_cluster": "rds-dig-stg-mro-it",  # target final name
        "tmp_cluster": "rds-dig-stg-mro-it-restore",
        "region": "eu-central-1",
    },
    "cross-account-mi3": {
        "description": "MI3 prod (legacy) -> staging (NewHorizon)",
        "source_profile": "iph",
        "dest_profile": "digital-webshop-staging/AWSAdministratorAccess",
        "source_cluster": "mi3-prod-eks-cluster",
        "dest_cluster": "rds-dig-stg-mro-mi3",
        "tmp_cluster": "rds-dig-stg-mro-mi3-restore",
        "region": "eu-central-1",
    },
}


def build_config(args) -> RefreshConfig:
    """Build RefreshConfig from arguments."""
    preset = PRESETS.get(args.scenario, {})

    source_profile = args.source_profile or preset.get("source_profile")
    dest_profile = args.dest_profile or preset.get("dest_profile")
    source_cluster_id = args.source_cluster or preset.get("source_cluster")
    dest_cluster_id = args.dest_cluster or preset.get("dest_cluster")
    tmp_cluster_id = args.tmp_cluster or preset.get("tmp_cluster") or f"{dest_cluster_id}-restore"
    region = args.region or preset.get("region", "eu-central-1")

    if not all([source_profile, dest_profile, source_cluster_id, dest_cluster_id]):
        print("Error: Missing required parameters. Use --help for usage.", file=sys.stderr)
        sys.exit(1)

    print(f"Building configuration...")
    if preset.get("description"):
        print(f"  Preset: {args.scenario} - {preset['description']}")

    # Get account IDs
    print(f"Fetching account info...")
    source_account_id = AWSClient.get_account_id(source_profile)
    dest_account_id = AWSClient.get_account_id(dest_profile)

    if not source_account_id:
        print(f"Error: Cannot get account ID for profile {source_profile}", file=sys.stderr)
        sys.exit(1)
    if not dest_account_id:
        print(f"Error: Cannot get account ID for profile {dest_profile}", file=sys.stderr)
        sys.exit(1)

    # Determine role names based on account
    source_role = get_role_name(source_account_id, "source")
    dest_role = get_role_name(dest_account_id, "destination")

    print(f"  Source: {source_account_id} (role: {source_role})")
    print(f"  Destination: {dest_account_id} (role: {dest_role})")

    # Get cluster info
    print(f"Fetching cluster info...")
    source_cluster = AWSClient.get_cluster_info(source_cluster_id, source_profile, region)
    dest_cluster = AWSClient.get_cluster_info(dest_cluster_id, dest_profile, region)

    if source_cluster:
        print(f"  Source cluster: {source_cluster_id} (found, {len(source_cluster.tags)} tags)")
    else:
        print(f"  Source cluster: {source_cluster_id} (not found, using defaults)")
        source_cluster = ClusterInfo(identifier=source_cluster_id)

    if dest_cluster:
        print(f"  Dest cluster: {dest_cluster_id} (found)")
    else:
        print(f"  Dest cluster: {dest_cluster_id} (not found, using placeholders)")
        dest_cluster = ClusterInfo(identifier=dest_cluster_id)

    return RefreshConfig(
        source=AccountConfig(
            account_id=source_account_id,
            role_arn=f"arn:aws:iam::{source_account_id}:role/{source_role}",
            profile=source_profile
        ),
        destination=AccountConfig(
            account_id=dest_account_id,
            role_arn=f"arn:aws:iam::{dest_account_id}:role/{dest_role}",
            profile=dest_profile
        ),
        source_cluster=source_cluster,
        dest_cluster=dest_cluster,
        tmp_cluster_identifier=tmp_cluster_id,
        region=region
    )


def list_presets():
    """List available presets."""
    print("Available presets:")
    print("-" * 60)
    for name, config in PRESETS.items():
        desc = config.get("description", "No description")
        print(f"  {name}")
        print(f"    {desc}")
        print(f"    {config['source_cluster']} -> {config['dest_cluster']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Generate refresh Step Function inputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use a preset
  %(prog)s --scenario mono-account -o ./test-mono-mi3
  %(prog)s --scenario cross-account-it -o ./test-cross-it

  # Custom configuration
  %(prog)s --source-cluster my-prod --dest-cluster my-staging \\
           --source-profile prod --dest-profile staging -o ./test-custom

  # List available presets
  %(prog)s --list-presets
"""
    )
    parser.add_argument("--scenario", choices=list(PRESETS.keys()),
                        help="Use a preset scenario")
    parser.add_argument("--source-cluster", help="Source DB cluster identifier")
    parser.add_argument("--dest-cluster", help="Destination DB cluster identifier (final name)")
    parser.add_argument("--tmp-cluster", help="Temporary cluster identifier during restore")
    parser.add_argument("--source-profile", help="AWS profile for source account")
    parser.add_argument("--dest-profile", help="AWS profile for destination account")
    parser.add_argument("--region", default="eu-central-1", help="AWS region")
    parser.add_argument("--output-dir", "-o", help="Output directory")
    parser.add_argument("--list-presets", action="store_true", help="List available presets")

    args = parser.parse_args()

    if args.list_presets:
        list_presets()
        sys.exit(0)

    if not args.output_dir:
        parser.error("--output-dir is required")

    if not args.scenario and not (args.source_cluster and args.dest_cluster):
        parser.print_help()
        sys.exit(1)

    config = build_config(args)
    generator = InputGenerator(config, Path(args.output_dir))
    generator.generate_all()


if __name__ == "__main__":
    main()
