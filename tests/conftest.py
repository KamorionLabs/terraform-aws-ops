"""
Pytest configuration and fixtures for Step Functions testing.
"""

import copy
import json
import os
import time
from pathlib import Path
from typing import Generator

import boto3
import pytest
from botocore.config import Config

# Step Functions Local endpoint
SFN_LOCAL_ENDPOINT = os.environ.get("SFN_LOCAL_ENDPOINT", "http://localhost:8083")

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


def get_asl_files() -> list[Path]:
    """Get all ASL files in the project."""
    asl_files = []
    for path in PROJECT_ROOT.rglob("*.asl.json"):
        if any(part.startswith('.') for part in path.parts):
            continue
        if 'node_modules' in path.parts or '.terraform' in path.parts:
            continue
        asl_files.append(path)
    return sorted(asl_files)


def strip_unsupported_fields(definition: dict) -> dict:
    """Remove fields not supported by Step Functions Local.

    SFN Local does not support:
    - Credentials (cross-account role assumption)
    - QueryLanguage/Arguments/Output/Assign/Condition (JSONata)

    This deep-cleans the definition so it can be loaded locally for structural
    validation (transitions, state names, error handling, branches).
    """
    cleaned = copy.deepcopy(definition)
    # Strip top-level QueryLanguage if present
    cleaned.pop("QueryLanguage", None)
    _strip_unsupported_from_states(cleaned.get("States", {}))
    # Final pass: sanitize any remaining JSONata expressions in the entire tree
    cleaned = _sanitize_jsonata_values(cleaned)
    return cleaned


# Keep old name as alias for backwards compatibility
strip_credentials = strip_unsupported_fields


def _sanitize_jsonata_values(obj):
    """Replace JSONata expression values ({% ... %}) with static placeholders."""
    if isinstance(obj, dict):
        return {k: _sanitize_jsonata_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_jsonata_values(item) for item in obj]
    elif isinstance(obj, str) and "{%" in obj:
        return "placeholder"
    return obj


def _strip_unsupported_from_states(states: dict) -> None:
    """Recursively remove unsupported fields from all states."""
    for state_def in states.values():
        state_def.pop("Credentials", None)
        state_def.pop("QueryLanguage", None)
        state_def.pop("Output", None)
        state_def.pop("Assign", None)

        # Convert JSONata Arguments → Parameters
        if "Arguments" in state_def:
            state_def["Parameters"] = state_def.pop("Arguments")

        # Convert JSONata Choice conditions to JSONPath-compatible form
        if state_def.get("Type") == "Choice" and "Choices" in state_def:
            for choice in state_def["Choices"]:
                if "Condition" in choice:
                    # JSONata Condition → dummy JSONPath condition
                    next_state = choice.get("Next", "")
                    choice.clear()
                    choice["Variable"] = "$.placeholder"
                    choice["IsPresent"] = True
                    choice["Next"] = next_state

        # Handle Parallel branches
        for branch in state_def.get("Branches", []):
            branch.pop("QueryLanguage", None)
            _strip_unsupported_from_states(branch.get("States", {}))

        # Handle Map Iterator (legacy)
        iterator = state_def.get("Iterator")
        if iterator:
            iterator.pop("QueryLanguage", None)
            _strip_unsupported_from_states(iterator.get("States", {}))

        # Handle Map ItemProcessor (new)
        item_processor = state_def.get("ItemProcessor")
        if item_processor:
            item_processor.pop("QueryLanguage", None)
            _strip_unsupported_from_states(item_processor.get("States", {}))


@pytest.fixture(scope="session")
def sfn_client():
    """Create Step Functions client connected to local endpoint."""
    config = Config(
        retries={'max_attempts': 3, 'mode': 'standard'}
    )
    client = boto3.client(
        'stepfunctions',
        endpoint_url=SFN_LOCAL_ENDPOINT,
        region_name='us-east-1',
        aws_access_key_id='testing',
        aws_secret_access_key='testing',
        config=config
    )
    return client


@pytest.fixture(scope="session")
def sfn_local_available(sfn_client) -> bool:
    """Check if Step Functions Local is available."""
    try:
        sfn_client.list_state_machines()
        return True
    except Exception:
        return False


@pytest.fixture
def create_state_machine(sfn_client):
    """Factory fixture to create state machines for testing."""
    created_arns = []

    def _create(name: str, definition: dict, role_arn: str = "arn:aws:iam::123456789012:role/test-role"):
        # Delete if exists (cleanup from previous failed tests)
        try:
            existing = sfn_client.list_state_machines()
            for sm in existing.get('stateMachines', []):
                if sm['name'] == name:
                    sfn_client.delete_state_machine(stateMachineArn=sm['stateMachineArn'])
                    time.sleep(0.5)  # Wait for deletion
        except Exception:
            pass

        # Strip Credentials blocks (not supported by Step Functions Local)
        clean_definition = strip_credentials(definition)

        response = sfn_client.create_state_machine(
            name=name,
            definition=json.dumps(clean_definition),
            roleArn=role_arn,
            type='STANDARD'
        )
        created_arns.append(response['stateMachineArn'])
        return response['stateMachineArn']

    yield _create

    # Cleanup
    for arn in created_arns:
        try:
            sfn_client.delete_state_machine(stateMachineArn=arn)
        except Exception:
            pass


@pytest.fixture
def sample_pass_definition() -> dict:
    """Simple Pass state machine for testing."""
    return {
        "Comment": "Simple pass-through for testing",
        "StartAt": "PassState",
        "States": {
            "PassState": {
                "Type": "Pass",
                "Result": {"status": "success"},
                "End": True
            }
        }
    }


@pytest.fixture
def sample_choice_definition() -> dict:
    """Choice state machine for testing branching logic."""
    return {
        "Comment": "Choice state for testing",
        "StartAt": "CheckValue",
        "States": {
            "CheckValue": {
                "Type": "Choice",
                "Choices": [
                    {
                        "Variable": "$.value",
                        "NumericGreaterThan": 10,
                        "Next": "HighValue"
                    }
                ],
                "Default": "LowValue"
            },
            "HighValue": {
                "Type": "Pass",
                "Result": "high",
                "End": True
            },
            "LowValue": {
                "Type": "Pass",
                "Result": "low",
                "End": True
            }
        }
    }


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "sfn_local: mark test as requiring Step Functions Local"
    )


def pytest_collection_modifyitems(config, items):
    """Skip sfn_local tests if endpoint not available."""
    # Check if SFN Local is available
    try:
        client = boto3.client(
            'stepfunctions',
            endpoint_url=SFN_LOCAL_ENDPOINT,
            region_name='us-east-1',
            aws_access_key_id='testing',
            aws_secret_access_key='testing'
        )
        client.list_state_machines()
        sfn_available = True
    except Exception:
        sfn_available = False

    if not sfn_available:
        skip_sfn = pytest.mark.skip(reason="Step Functions Local not available")
        for item in items:
            if "sfn_local" in item.keywords:
                item.add_marker(skip_sfn)
