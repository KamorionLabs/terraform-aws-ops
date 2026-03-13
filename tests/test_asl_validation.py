"""
Unit tests for ASL validation - runs without AWS credentials.
Tests the structure and validity of all ASL files in the project.
"""

import json
from pathlib import Path

import pytest

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


def get_asl_files() -> list[tuple[str, Path]]:
    """Get all ASL files with their module name for parametrization."""
    asl_files = []
    for path in PROJECT_ROOT.rglob("*.asl.json"):
        if any(part.startswith('.') for part in path.parts):
            continue
        if 'node_modules' in path.parts or '.terraform' in path.parts:
            continue
        # Create a readable test ID from the path
        relative = path.relative_to(PROJECT_ROOT)
        test_id = str(relative).replace('/', '_').replace('.asl.json', '')
        asl_files.append((test_id, path))
    return sorted(asl_files, key=lambda x: x[0])


# Get all ASL files for parametrization
ASL_FILES = get_asl_files()


class TestASLJsonSyntax:
    """Test JSON syntax validity."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_valid_json(self, test_id, asl_file):
        """Each ASL file must be valid JSON."""
        with open(asl_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Should not raise
        data = json.loads(content)
        assert isinstance(data, dict), "ASL definition must be a JSON object"


class TestASLRequiredFields:
    """Test required ASL fields."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_has_start_at(self, test_id, asl_file):
        """Each ASL file must have StartAt field."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        assert "StartAt" in data, "Missing required field: StartAt"
        assert isinstance(data["StartAt"], str), "StartAt must be a string"
        assert len(data["StartAt"]) > 0, "StartAt cannot be empty"

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_has_states(self, test_id, asl_file):
        """Each ASL file must have States field."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        assert "States" in data, "Missing required field: States"
        assert isinstance(data["States"], dict), "States must be an object"
        assert len(data["States"]) > 0, "States cannot be empty"

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_start_at_exists(self, test_id, asl_file):
        """StartAt must reference an existing state."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        start_at = data.get("StartAt")
        states = data.get("States", {})

        assert start_at in states, f"StartAt '{start_at}' references non-existent state"


class TestASLStateTypes:
    """Test state type validity."""

    VALID_TYPES = {"Task", "Pass", "Choice", "Wait", "Succeed", "Fail", "Parallel", "Map"}

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_valid_state_types(self, test_id, asl_file):
        """All states must have valid Type."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        for state_name, state_def in data.get("States", {}).items():
            assert "Type" in state_def, f"State '{state_name}' missing Type"
            assert state_def["Type"] in self.VALID_TYPES, \
                f"State '{state_name}' has invalid Type: {state_def['Type']}"


class TestASLStateTransitions:
    """Test state transitions and references."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_next_references_valid(self, test_id, asl_file):
        """All Next references must point to existing states."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})
        valid_states = set(states.keys())

        for state_name, state_def in states.items():
            # Check direct Next
            if "Next" in state_def:
                assert state_def["Next"] in valid_states, \
                    f"State '{state_name}' Next references non-existent state: {state_def['Next']}"

            # Check Choice transitions
            if state_def.get("Type") == "Choice":
                for i, choice in enumerate(state_def.get("Choices", [])):
                    if "Next" in choice:
                        assert choice["Next"] in valid_states, \
                            f"State '{state_name}' Choice[{i}] references non-existent state: {choice['Next']}"

                if "Default" in state_def:
                    assert state_def["Default"] in valid_states, \
                        f"State '{state_name}' Default references non-existent state: {state_def['Default']}"

            # Check Catch transitions
            for i, catch in enumerate(state_def.get("Catch", [])):
                if "Next" in catch:
                    assert catch["Next"] in valid_states, \
                        f"State '{state_name}' Catch[{i}] references non-existent state: {catch['Next']}"

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_has_terminal_state(self, test_id, asl_file):
        """Workflow must have at least one terminal state."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})
        has_terminal = False

        for state_name, state_def in states.items():
            state_type = state_def.get("Type", "")
            if state_type in ("Succeed", "Fail") or state_def.get("End", False):
                has_terminal = True
                break

        assert has_terminal, "No terminal state found (End: true, Succeed, or Fail)"

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_non_terminal_states_have_transition(self, test_id, asl_file):
        """Non-terminal states must have Next or End."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})
        no_transition_types = {"Choice", "Succeed", "Fail"}

        for state_name, state_def in states.items():
            state_type = state_def.get("Type", "")

            if state_type not in no_transition_types:
                has_end = state_def.get("End", False)
                has_next = "Next" in state_def

                assert has_end or has_next, \
                    f"State '{state_name}' (Type: {state_type}) must have 'End: true' or 'Next'"


class TestASLTaskStates:
    """Test Task state specific requirements."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_task_has_resource(self, test_id, asl_file):
        """Task states must have Resource field."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})

        for state_name, state_def in states.items():
            if state_def.get("Type") == "Task":
                assert "Resource" in state_def, \
                    f"Task state '{state_name}' missing required Resource field"


class TestASLChoiceStates:
    """Test Choice state specific requirements."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_choice_has_choices(self, test_id, asl_file):
        """Choice states must have Choices array."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})

        for state_name, state_def in states.items():
            if state_def.get("Type") == "Choice":
                assert "Choices" in state_def, \
                    f"Choice state '{state_name}' missing required Choices field"
                assert isinstance(state_def["Choices"], list), \
                    f"Choice state '{state_name}' Choices must be an array"
                assert len(state_def["Choices"]) > 0, \
                    f"Choice state '{state_name}' Choices cannot be empty"


class TestASLCrossAccount:
    """Test cross-account patterns."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_credentials_have_role_arn(self, test_id, asl_file):
        """States with Credentials must have RoleArn."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})

        for state_name, state_def in states.items():
            if "Credentials" in state_def:
                creds = state_def["Credentials"]
                has_role = "RoleArn" in creds or "RoleArn.$" in creds
                assert has_role, \
                    f"State '{state_name}' has Credentials but no RoleArn"


class TestASLComment:
    """Test that ASL files have a Comment field (SUB-04 readiness)."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_has_comment(self, test_id, asl_file):
        """Each ASL file should have a top-level Comment field describing its purpose."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        assert "Comment" in data, \
            f"ASL file missing top-level 'Comment' field: {asl_file.name}"
        assert isinstance(data["Comment"], str), \
            f"Comment must be a string in: {asl_file.name}"
        assert len(data["Comment"].strip()) > 0, \
            f"Comment cannot be empty in: {asl_file.name}"


class TestASLCatchSelfContained:
    """Test that sub-SFN Fail states have named errors (SUB-05 readiness).

    Only applies to EFS manage_*.asl.json files (sub-SFNs).
    Existing SFNs are not required to follow this pattern.
    """

    @staticmethod
    def _get_sub_sfn_files() -> list[tuple[str, Path]]:
        """Get EFS sub-SFN files (manage_*.asl.json) for parametrization."""
        sub_sfn_files = []
        efs_dir = PROJECT_ROOT / "modules" / "step-functions" / "efs"
        if efs_dir.exists():
            for path in sorted(efs_dir.glob("manage_*.asl.json")):
                test_id = path.stem
                sub_sfn_files.append((test_id, path))
        return sub_sfn_files

    SUB_SFN_FILES = _get_sub_sfn_files.__func__()

    @pytest.mark.parametrize(
        "test_id,asl_file",
        SUB_SFN_FILES,
        ids=[f[0] for f in SUB_SFN_FILES],
    )
    def test_fail_states_have_named_error(self, test_id, asl_file):
        """Fail states in sub-SFNs must have a non-empty Error field for proper catch routing."""
        with open(asl_file, 'r') as f:
            data = json.load(f)

        states = data.get("States", {})

        for state_name, state_def in states.items():
            if state_def.get("Type") == "Fail":
                assert "Error" in state_def, \
                    f"Fail state '{state_name}' missing 'Error' field in sub-SFN {asl_file.name}"
                assert isinstance(state_def["Error"], str) and len(state_def["Error"].strip()) > 0, \
                    f"Fail state '{state_name}' has empty 'Error' in sub-SFN {asl_file.name}"
