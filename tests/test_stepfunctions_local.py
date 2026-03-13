"""
Integration tests using Step Functions Local (Docker).

These tests require the aws-stepfunctions-local Docker container to be running:
    docker run -p 8083:8083 amazon/aws-stepfunctions-local

Tests are automatically skipped if the container is not available.
"""

import json
import time
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
        relative = path.relative_to(PROJECT_ROOT)
        test_id = str(relative).replace('/', '_').replace('.asl.json', '')
        asl_files.append((test_id, path))
    return sorted(asl_files, key=lambda x: x[0])


ASL_FILES = get_asl_files()


@pytest.mark.sfn_local
class TestStateMachineCreation:
    """Test that state machines can be created in Step Functions Local."""

    @pytest.mark.parametrize("test_id,asl_file", ASL_FILES, ids=[f[0] for f in ASL_FILES])
    def test_create_state_machine(self, test_id, asl_file, create_state_machine):
        """Each ASL file should be accepted by Step Functions Local.

        Uses create_state_machine fixture which strips Credentials blocks
        (not supported by Step Functions Local) via conftest.strip_credentials.
        """
        with open(asl_file, 'r') as f:
            definition = json.load(f)

        # Use a unique name to avoid conflicts
        sm_name = f"test-{test_id}-{int(time.time())}"

        sm_arn = create_state_machine(sm_name, definition)
        assert sm_arn is not None
        assert sm_arn.endswith(sm_name)


@pytest.mark.sfn_local
class TestSimpleWorkflows:
    """Test simple workflow execution patterns."""

    def test_pass_state_execution(self, sfn_client, create_state_machine, sample_pass_definition):
        """Test execution of a simple Pass state machine."""
        sm_arn = create_state_machine("test-pass", sample_pass_definition)

        # Start execution
        exec_response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"test": "data"})
        )

        execution_arn = exec_response['executionArn']

        # Wait for completion (with timeout)
        max_wait = 10
        waited = 0
        while waited < max_wait:
            desc = sfn_client.describe_execution(executionArn=execution_arn)
            if desc['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
                break
            time.sleep(0.5)
            waited += 0.5

        # Verify success
        final_status = sfn_client.describe_execution(executionArn=execution_arn)
        assert final_status['status'] == 'SUCCEEDED', \
            f"Expected SUCCEEDED but got {final_status['status']}"

        # Check output
        output = json.loads(final_status['output'])
        assert output == {"status": "success"}

    def test_choice_state_high_value(self, sfn_client, create_state_machine, sample_choice_definition):
        """Test Choice state with high value branch."""
        sm_arn = create_state_machine("test-choice-high", sample_choice_definition)

        exec_response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"value": 15})  # > 10, should go to HighValue
        )

        execution_arn = exec_response['executionArn']

        # Wait for completion
        max_wait = 10
        waited = 0
        while waited < max_wait:
            desc = sfn_client.describe_execution(executionArn=execution_arn)
            if desc['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
                break
            time.sleep(0.5)
            waited += 0.5

        final_status = sfn_client.describe_execution(executionArn=execution_arn)
        assert final_status['status'] == 'SUCCEEDED'

        output = json.loads(final_status['output'])
        assert output == "high"

    def test_choice_state_low_value(self, sfn_client, create_state_machine, sample_choice_definition):
        """Test Choice state with low value branch (default)."""
        sm_arn = create_state_machine("test-choice-low", sample_choice_definition)

        exec_response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"value": 5})  # <= 10, should go to LowValue (default)
        )

        execution_arn = exec_response['executionArn']

        # Wait for completion
        max_wait = 10
        waited = 0
        while waited < max_wait:
            desc = sfn_client.describe_execution(executionArn=execution_arn)
            if desc['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
                break
            time.sleep(0.5)
            waited += 0.5

        final_status = sfn_client.describe_execution(executionArn=execution_arn)
        assert final_status['status'] == 'SUCCEEDED'

        output = json.loads(final_status['output'])
        assert output == "low"


@pytest.mark.sfn_local
class TestMockedServiceIntegrations:
    """
    Test state machines with mocked service integrations.

    Note: Step Functions Local can mock AWS service integrations.
    See: https://docs.aws.amazon.com/step-functions/latest/dg/sfn-local-config-options.html
    """

    def test_task_state_with_mock(self, sfn_client, create_state_machine):
        """Test Task state with mocked response."""
        # This definition uses a Lambda task that we'll mock
        definition = {
            "Comment": "Task with mock",
            "StartAt": "MockedTask",
            "States": {
                "MockedTask": {
                    "Type": "Task",
                    "Resource": "arn:aws:lambda:us-east-1:123456789012:function:test",
                    "End": True
                }
            }
        }

        sm_arn = create_state_machine("test-mock-task", definition)

        # Note: Without proper mock configuration, this will fail
        # In CI, we'd configure MockConfigFile for Step Functions Local
        # For now, we just verify the state machine can be created

        # This test demonstrates the pattern - actual mocking requires
        # Step Functions Local mock configuration file
        assert sm_arn is not None


@pytest.mark.sfn_local
class TestErrorHandling:
    """Test error handling patterns."""

    def test_fail_state(self, sfn_client, create_state_machine):
        """Test Fail state execution."""
        definition = {
            "Comment": "Fail state test",
            "StartAt": "FailState",
            "States": {
                "FailState": {
                    "Type": "Fail",
                    "Error": "TestError",
                    "Cause": "This is a test failure"
                }
            }
        }

        sm_arn = create_state_machine("test-fail", definition)

        exec_response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({})
        )

        execution_arn = exec_response['executionArn']

        # Wait for completion
        max_wait = 10
        waited = 0
        while waited < max_wait:
            desc = sfn_client.describe_execution(executionArn=execution_arn)
            if desc['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
                break
            time.sleep(0.5)
            waited += 0.5

        final_status = sfn_client.describe_execution(executionArn=execution_arn)
        assert final_status['status'] == 'FAILED'
        assert final_status.get('error') == 'TestError'

    def test_succeed_state(self, sfn_client, create_state_machine):
        """Test Succeed state execution."""
        definition = {
            "Comment": "Succeed state test",
            "StartAt": "SucceedState",
            "States": {
                "SucceedState": {
                    "Type": "Succeed"
                }
            }
        }

        sm_arn = create_state_machine("test-succeed", definition)

        exec_response = sfn_client.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({"data": "test"})
        )

        execution_arn = exec_response['executionArn']

        # Wait for completion
        max_wait = 10
        waited = 0
        while waited < max_wait:
            desc = sfn_client.describe_execution(executionArn=execution_arn)
            if desc['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
                break
            time.sleep(0.5)
            waited += 0.5

        final_status = sfn_client.describe_execution(executionArn=execution_arn)
        assert final_status['status'] == 'SUCCEEDED'
