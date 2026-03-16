"""
Unit tests for sync_config_items Lambda stub.
Tests the input/output contract that Phase 5 will implement.
"""

import re
import sys
from pathlib import Path

import pytest

# Add Lambda source to path (same pattern as other Lambda tests)
sys.path.insert(0, str(Path(__file__).parent.parent / "lambdas" / "sync-config-items"))

from sync_config_items import lambda_handler


class TestSyncConfigItemsStub:
    """Test the stub Lambda returns the correct structured output."""

    def test_stub_returns_structured_output_sm(self):
        """lambda_handler with SecretsManager input returns statusCode=200 and correct result."""
        event = {
            "Item": {
                "Type": "SecretsManager",
                "SourcePath": "/app/my-secret",
                "DestinationPath": "/dest/my-secret",
                "Transforms": {},
            },
            "SourceAccount": {
                "AccountId": "111111111111",
                "RoleArn": "arn:aws:iam::111111111111:role/source-role",
                "Region": "eu-central-1",
            },
            "DestinationAccount": {
                "AccountId": "222222222222",
                "RoleArn": "arn:aws:iam::222222222222:role/dest-role",
                "Region": "eu-central-1",
            },
        }

        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] == "skipped"
        assert result["result"]["type"] == "SecretsManager"

    def test_stub_returns_structured_output_ssm(self):
        """lambda_handler with SSMParameter input returns statusCode=200 and correct result."""
        event = {
            "Item": {
                "Type": "SSMParameter",
                "SourcePath": "/config/param1",
                "DestinationPath": "/dest/config/param1",
                "Transforms": {},
            },
            "SourceAccount": {
                "AccountId": "111111111111",
                "RoleArn": "arn:aws:iam::111111111111:role/source-role",
                "Region": "eu-central-1",
            },
            "DestinationAccount": {
                "AccountId": "222222222222",
                "RoleArn": "arn:aws:iam::222222222222:role/dest-role",
                "Region": "eu-central-1",
            },
        }

        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] == "skipped"
        assert result["result"]["type"] == "SSMParameter"

    def test_stub_handles_empty_input(self):
        """lambda_handler with empty input (no Item) returns skipped with unknown type."""
        event = {}

        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] == "skipped"
        assert result["result"]["type"] == "unknown"
        assert result["result"]["source"] == ""
        assert result["result"]["destination"] == ""

    def test_stub_returns_correct_paths(self):
        """lambda_handler returns result.source == Item.SourcePath and result.destination == Item.DestinationPath."""
        event = {
            "Item": {
                "Type": "SecretsManager",
                "SourcePath": "/source/path/secret-a",
                "DestinationPath": "/destination/path/secret-a",
                "Transforms": {},
            },
            "SourceAccount": {"AccountId": "111", "RoleArn": "arn:...", "Region": "eu-central-1"},
            "DestinationAccount": {"AccountId": "222", "RoleArn": "arn:...", "Region": "eu-central-1"},
        }

        result = lambda_handler(event, None)

        assert result["result"]["source"] == "/source/path/secret-a"
        assert result["result"]["destination"] == "/destination/path/secret-a"

    def test_stub_message_contains_stub(self):
        """lambda_handler returns result.message containing 'Stub'."""
        event = {
            "Item": {
                "Type": "SecretsManager",
                "SourcePath": "/src",
                "DestinationPath": "/dst",
                "Transforms": {},
            },
            "SourceAccount": {},
            "DestinationAccount": {},
        }

        result = lambda_handler(event, None)

        assert "Stub" in result["result"]["message"]

    def test_no_hardcoded_client_names(self):
        """Lambda source code must not contain hardcoded client-specific names."""
        lambda_file = Path(__file__).parent.parent / "lambdas" / "sync-config-items" / "sync_config_items.py"
        content = lambda_file.read_text()

        # Check for hardcoded client-specific names (case-insensitive)
        for pattern in ["rubix", "bene", "homebox"]:
            matches = re.findall(pattern, content, re.IGNORECASE)
            assert len(matches) == 0, (
                f"Found hardcoded client name '{pattern}' in Lambda source code"
            )
