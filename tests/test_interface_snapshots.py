"""
Interface non-regression tests (REF-05).

Captures output schemas from ASL files BEFORE refactoring as snapshots,
then verifies they remain identical AFTER refactoring.

On first run (snapshot does not exist), creates the snapshot (bootstrap mode).
On subsequent runs, asserts current schema matches snapshot exactly.
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"

# ASL files being refactored across all Phase 2 plans
REFACTORED_FILES = {
    "check_replication_sync": PROJECT_ROOT / "modules" / "step-functions" / "efs" / "check_replication_sync.asl.json",
    "setup_cross_account_replication": PROJECT_ROOT / "modules" / "step-functions" / "efs" / "setup_cross_account_replication.asl.json",
    "prepare_snapshot_for_restore": PROJECT_ROOT / "modules" / "step-functions" / "db" / "prepare_snapshot_for_restore.asl.json",
    "restore_cluster": PROJECT_ROOT / "modules" / "step-functions" / "db" / "restore_cluster.asl.json",
    "refresh_orchestrator": PROJECT_ROOT / "modules" / "step-functions" / "orchestrator" / "refresh_orchestrator.asl.json",
}


def extract_output_schema(asl_data: dict) -> dict:
    """Extract the output schema from an ASL definition.

    Finds terminal output states:
    - Succeed states (no output keys, but presence is noted)
    - Pass states with End: true (captures Parameters keys)
    - Pass states whose Next points to a Succeed state (captures Parameters keys)
    - Also captures the top-level Comment field (I/O contract documentation)

    Returns a dict with:
      - comment: top-level Comment string
      - terminal_outputs: dict mapping state name -> sorted list of output keys
    """
    states = asl_data.get("States", {})

    # Find all Succeed state names
    succeed_states = {
        name for name, sdef in states.items()
        if sdef.get("Type") == "Succeed"
    }

    terminal_outputs = {}

    for name, sdef in states.items():
        state_type = sdef.get("Type", "")

        # Succeed states themselves (note presence but no output keys)
        if state_type == "Succeed":
            terminal_outputs[name] = []
            continue

        # Fail states -- they are terminal but not output states
        if state_type == "Fail":
            continue

        # Pass states with End: true or whose Next is a Succeed state
        if state_type == "Pass":
            is_terminal = sdef.get("End", False)
            next_state = sdef.get("Next", "")
            feeds_succeed = next_state in succeed_states

            if is_terminal or feeds_succeed:
                # Extract output keys from Parameters
                params = sdef.get("Parameters", {})
                keys = sorted(params.keys())
                # Strip .$ suffixes for comparison (e.g., "Status.$" -> "Status")
                clean_keys = sorted(set(
                    k.rstrip(".$") if k.endswith(".$") else k
                    for k in keys
                ))
                terminal_outputs[name] = clean_keys

    return {
        "comment": asl_data.get("Comment", ""),
        "terminal_outputs": terminal_outputs,
    }


def get_snapshot_path(sfn_name: str) -> Path:
    """Get the snapshot file path for a given SFN name."""
    return SNAPSHOTS_DIR / f"{sfn_name}_outputs.json"


def load_or_create_snapshot(sfn_name: str, current_schema: dict) -> dict:
    """Load existing snapshot or create it if it doesn't exist (bootstrap)."""
    snapshot_path = get_snapshot_path(sfn_name)

    if snapshot_path.exists():
        with open(snapshot_path, "r") as f:
            return json.load(f)

    # Bootstrap mode: create snapshot
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(snapshot_path, "w") as f:
        json.dump(current_schema, f, indent=2, sort_keys=True)
        f.write("\n")

    return current_schema


# Build parametrized test data
SNAPSHOT_TEST_CASES = []
for sfn_name, sfn_path in REFACTORED_FILES.items():
    if sfn_path.exists():
        SNAPSHOT_TEST_CASES.append((sfn_name, sfn_path))


@pytest.mark.parametrize(
    "sfn_name,sfn_path",
    SNAPSHOT_TEST_CASES,
    ids=[t[0] for t in SNAPSHOT_TEST_CASES],
)
class TestInterfaceSnapshots:
    """Verify output schemas match pre-refactoring snapshots."""

    def test_output_schema_unchanged(self, sfn_name, sfn_path):
        """Output schema must match the snapshot captured before refactoring."""
        with open(sfn_path, "r") as f:
            asl_data = json.load(f)

        current_schema = extract_output_schema(asl_data)
        snapshot = load_or_create_snapshot(sfn_name, current_schema)

        assert current_schema == snapshot, (
            f"Output schema changed for {sfn_name}!\n"
            f"Expected (snapshot): {json.dumps(snapshot, indent=2)}\n"
            f"Got (current): {json.dumps(current_schema, indent=2)}"
        )

    def test_has_terminal_outputs(self, sfn_name, sfn_path):
        """Refactored SFN must still have at least one terminal output state."""
        with open(sfn_path, "r") as f:
            asl_data = json.load(f)

        schema = extract_output_schema(asl_data)
        assert len(schema["terminal_outputs"]) > 0, (
            f"{sfn_name} has no terminal output states"
        )
