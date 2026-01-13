#!/usr/bin/env python3
"""
Generate JSON input files for refresh Step Functions.

Usage:
    python generate_refresh_inputs.py --scenario mono-account --output-dir ./test-mi3
    python generate_refresh_inputs.py --scenario cross-account --output-dir ./test-cross
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
from typing import Optional


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
    kms_key_id: Optional[str] = None


@dataclass
class RefreshConfig:
    source: AccountConfig
    destination: AccountConfig
    source_cluster: ClusterInfo
    dest_cluster: ClusterInfo
    region: str
    orchestrator_account: str = "025922408720"
    orchestrator_profile: str = "shared-services/AWSAdministratorAccess"
    sfn_prefix: str = "sfn-dig-tooling-refresh"

    @property
    def is_same_account(self) -> bool:
        return self.source.account_id == self.destination.account_id

    @property
    def tmp_cluster_id(self) -> str:
        return f"{self.dest_cluster.identifier}-restore"

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
        return ClusterInfo(
            identifier=cluster_id,
            engine=cluster.get("Engine"),
            engine_version=cluster.get("EngineVersion"),
            security_groups=[sg["VpcSecurityGroupId"] for sg in cluster.get("VpcSecurityGroups", [])],
            subnet_group=cluster.get("DBSubnetGroup"),
            parameter_group=cluster.get("DBClusterParameterGroup"),
            kms_key_id=cluster.get("KmsKeyId")
        )

    @classmethod
    def get_latest_snapshot(cls, cluster_id: str, profile: str, region: str) -> Optional[str]:
        result = cls.run([
            "rds", "describe-db-cluster-snapshots",
            "--db-cluster-identifier", cluster_id,
            "--snapshot-type", "automated",
            "--query", "DBClusterSnapshots[-1].DBClusterSnapshotIdentifier"
        ], profile, region)
        return result if isinstance(result, str) else None

    @classmethod
    def get_sfn_arn(cls, sfn_name: str, profile: str, region: str) -> Optional[str]:
        result = cls.run([
            "stepfunctions", "list-state-machines",
            "--query", f"stateMachines[?name=='{sfn_name}'].stateMachineArn | [0]"
        ], profile, region)
        return result if isinstance(result, str) and result != "None" else None


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
            "PrepareSnapshotStateMachineArn": self.config.prepare_sfn_arn
        })

    def _generate_phase1_db(self):
        dest = self.config.destination.to_dict()
        src = self.config.source.to_dict()
        dc = self.config.dest_cluster

        # 01 - Ensure cluster not exists
        self._write_json(self.output_dir / "phase1-db/01-ensure-cluster-not-exists.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": self.config.tmp_cluster_id
        })

        # 02 - Restore cluster
        self._write_json(self.output_dir / "phase1-db/02-restore-cluster.json", {
            "SourceAccount": src,
            "DestinationAccount": dest,
            "PrepareSnapshotStateMachineArn": self.config.prepare_sfn_arn,
            "SourceDBClusterIdentifier": self.config.source_cluster.identifier,
            "TmpDbClusterIdentifier": self.config.tmp_cluster_id,
            "RestoreType": "from-snapshot",
            "SnapshotMode": "copy-latest",
            "DbInstanceClass": "db.serverless",
            "AuroraServerlessMinCapacity": 0.5,
            "AuroraServerlessMaxCapacity": 6,
            "DbSubnetGroupName": dc.subnet_group or "PLACEHOLDER",
            "VpcSecurityGroupIds": dc.security_groups or ["sg-PLACEHOLDER"],
            "DbClusterParameterGroupName": dc.parameter_group or "PLACEHOLDER",
            "KmsKeyId": dc.kms_key_id or "alias/aws/rds"
        })

        # 03 - Create instance
        instance_prefix = self.config.dest_cluster.identifier.split("-")[0]
        self._write_json(self.output_dir / "phase1-db/03-create-instance.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": self.config.tmp_cluster_id,
            "DbInstanceClass": "db.serverless",
            "Engine": "aurora-mysql",
            "DbInstances": [
                {"DbInstanceIdentifier": f"{instance_prefix}-restore-0"}
            ]
        })

        # 04 - Enable master secret
        self._write_json(self.output_dir / "phase1-db/04-enable-master-secret.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": self.config.tmp_cluster_id,
            "Region": self.config.region,
            "KmsKeyId": "alias/aws/secretsmanager"
        })

    def _generate_phase2_switch(self):
        dest = self.config.destination.to_dict()
        dc_id = self.config.dest_cluster.identifier
        instance_prefix = dc_id.split("-")[0]

        # 01 - Rename old cluster
        self._write_json(self.output_dir / "phase2-switch/01-rename-old-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": dc_id,
            "NewDbClusterIdentifier": f"{dc_id}-old",
            "NewInstanceIdentifierPrefix": f"{instance_prefix}-old"
        })

        # 02 - Rename new cluster
        self._write_json(self.output_dir / "phase2-switch/02-rename-new-cluster.json", {
            "DestinationAccount": dest,
            "DbClusterIdentifier": self.config.tmp_cluster_id,
            "NewDbClusterIdentifier": dc_id,
            "NewInstanceIdentifierPrefix": instance_prefix
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
echo "Monitor: aws stepfunctions describe-execution --execution-arn '$EXEC_ARN' --query 'status' --profile $AWS_PROFILE --region $AWS_REGION"
'''
        script_path = self.output_dir / "scripts/run-step.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)
        print(f"  Generated: scripts/run-step.sh")

    def _print_summary(self):
        print("\n" + "=" * 50)
        print(f"Generated inputs in: {self.output_dir}")
        print("=" * 50)
        print(f"Source: {self.config.source_cluster.identifier} ({self.config.source.account_id})")
        print(f"Destination: {self.config.dest_cluster.identifier} ({self.config.destination.account_id})")
        print(f"Mode: {'Same-account' if self.config.is_same_account else 'Cross-account'}")
        print("\nTo run:")
        print(f"  cd {self.output_dir}")
        print("  ./scripts/run-step.sh db-ensure-cluster-not-exists phase1-db/01-ensure-cluster-not-exists.json")


# =============================================================================
# Presets
# =============================================================================

PRESETS = {
    "mono-account": {
        "source_profile": "iph",
        "dest_profile": "iph",
        "source_cluster": "mi3-prod-eks-cluster",
        "dest_cluster": "mi3-staging-eks-cluster",
        "region": "eu-central-1",
        "source_role": "rubix-refresh-source-role",
        "dest_role": "rubix-refresh-destination-role",
    },
    "cross-account": {
        "source_profile": "iph",
        "dest_profile": "digital-webshop-staging/AWSAdministratorAccess",
        "source_cluster": "it-prod-eks-cluster",
        "dest_cluster": "rubix-dig-stg-aurora",
        "region": "eu-central-1",
        "source_role": "rubix-refresh-source-role",
        "dest_role": "refresh-destination-role",
    },
}


def build_config(args) -> RefreshConfig:
    """Build RefreshConfig from arguments."""
    preset = PRESETS.get(args.scenario, {})

    source_profile = args.source_profile or preset.get("source_profile")
    dest_profile = args.dest_profile or preset.get("dest_profile")
    source_cluster_id = args.source_cluster or preset.get("source_cluster")
    dest_cluster_id = args.dest_cluster or preset.get("dest_cluster")
    region = args.region or preset.get("region", "eu-central-1")
    source_role = preset.get("source_role", "rubix-refresh-source-role")
    dest_role = preset.get("dest_role", "refresh-destination-role")

    if not all([source_profile, dest_profile, source_cluster_id, dest_cluster_id]):
        print("Error: Missing required parameters. Use --help for usage.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching account info...")

    # Get account IDs
    source_account_id = AWSClient.get_account_id(source_profile)
    dest_account_id = AWSClient.get_account_id(dest_profile)

    if not source_account_id:
        print(f"Error: Cannot get account ID for profile {source_profile}", file=sys.stderr)
        sys.exit(1)
    if not dest_account_id:
        print(f"Error: Cannot get account ID for profile {dest_profile}", file=sys.stderr)
        sys.exit(1)

    print(f"  Source: {source_account_id} ({source_profile})")
    print(f"  Destination: {dest_account_id} ({dest_profile})")

    # Get cluster info
    print(f"Fetching cluster info...")
    source_cluster = AWSClient.get_cluster_info(source_cluster_id, source_profile, region)
    dest_cluster = AWSClient.get_cluster_info(dest_cluster_id, dest_profile, region)

    if source_cluster:
        print(f"  Source cluster: {source_cluster_id} (found)")
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
        region=region
    )


def main():
    parser = argparse.ArgumentParser(description="Generate refresh Step Function inputs")
    parser.add_argument("--scenario", choices=["mono-account", "cross-account", "custom"],
                        help="Use a preset scenario")
    parser.add_argument("--source-cluster", help="Source DB cluster identifier")
    parser.add_argument("--dest-cluster", help="Destination DB cluster identifier")
    parser.add_argument("--source-profile", help="AWS profile for source account")
    parser.add_argument("--dest-profile", help="AWS profile for destination account")
    parser.add_argument("--region", default="eu-central-1", help="AWS region")
    parser.add_argument("--output-dir", "-o", required=True, help="Output directory")

    args = parser.parse_args()

    if not args.scenario and not (args.source_cluster and args.dest_cluster):
        parser.print_help()
        sys.exit(1)

    config = build_config(args)
    generator = InputGenerator(config, Path(args.output_dir))
    generator.generate_all()


if __name__ == "__main__":
    main()
