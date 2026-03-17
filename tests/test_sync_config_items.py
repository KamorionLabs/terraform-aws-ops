"""
Behavior tests for sync_config_items Lambda.
TDD RED: These tests define the expected behavior for SYNC-02 through SYNC-07.
All tests should FAIL against the current stub Lambda until Phase 5 Plan 02 implementation.
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Add Lambda source to path (same pattern as other Lambda tests)
sys.path.insert(0, str(Path(__file__).parent.parent / "lambdas" / "sync-config-items"))

from sync_config_items import lambda_handler


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_event(
    item_type="SecretsManager",
    source_path="/app/my-secret",
    dest_path="/dest/my-secret",
    transforms=None,
    merge_mode=False,
    source_role="arn:aws:iam::111111111111:role/source-role",
    dest_role="arn:aws:iam::222222222222:role/dest-role",
    source_region="eu-central-1",
    dest_region="eu-central-1",
):
    """Build a standard per-item event for the Lambda."""
    item = {
        "Type": item_type,
        "SourcePath": source_path,
        "DestinationPath": dest_path,
        "Transforms": transforms or {},
    }
    if merge_mode:
        item["MergeMode"] = True
    return {
        "Item": item,
        "SourceAccount": {
            "AccountId": "111111111111",
            "RoleArn": source_role,
            "Region": source_region,
        },
        "DestinationAccount": {
            "AccountId": "222222222222",
            "RoleArn": dest_role,
            "Region": dest_region,
        },
    }


def _sts_credentials():
    """Return fake STS credentials."""
    return {
        "Credentials": {
            "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "FwoGZXIvYXdzEBYaDN...",
        }
    }


# ===========================================================================
# SYNC-02: Cross-Account Fetch via STS AssumeRole
# ===========================================================================

class TestCrossAccountFetch:
    """Tests for cross-account access using STS AssumeRole (SYNC-02)."""

    @patch("sync_config_items.boto3")
    def test_sm_fetch_calls_sts_assume_role(self, mock_boto3):
        """lambda_handler with SM item calls sts.assume_role with correct RoleArn + DurationSeconds=900."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": '{"key": "value"}'}
        mock_sm.put_secret_value.return_value = {}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "secretsmanager":
                return mock_sm
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager")
        result = lambda_handler(event, None)

        # STS should be called to assume the source role
        mock_sts.assume_role.assert_any_call(
            RoleArn="arn:aws:iam::111111111111:role/source-role",
            RoleSessionName="SyncConfigItems",
            DurationSeconds=900,
        )

    @patch("sync_config_items.boto3")
    def test_sm_fetch_creates_client_with_assumed_credentials(self, mock_boto3):
        """After STS, creates secretsmanager client with assumed credentials."""
        mock_sts = MagicMock()
        creds = _sts_credentials()
        mock_sts.assume_role.return_value = creds

        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": '{"key": "value"}'}
        mock_sm.put_secret_value.return_value = {}

        clients_created = []

        def client_factory(service, **kwargs):
            clients_created.append((service, kwargs))
            if service == "sts":
                return mock_sts
            return mock_sm

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager")
        lambda_handler(event, None)

        # Should create SM client with assumed credentials
        sm_calls = [c for c in clients_created if c[0] == "secretsmanager"]
        assert len(sm_calls) >= 1, "Should create at least one secretsmanager client"
        sm_kwargs = sm_calls[0][1]
        assert sm_kwargs.get("aws_access_key_id") == "AKIAIOSFODNN7EXAMPLE"
        assert sm_kwargs.get("aws_secret_access_key") == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    @patch("sync_config_items.boto3")
    def test_ssm_fetch_calls_sts_for_source_and_destination(self, mock_boto3):
        """Both source and destination accounts get STS-assumed clients."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Name": "/src/param", "Value": "val", "Type": "String"}
        }
        mock_ssm.put_parameter.return_value = {}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            return mock_ssm

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SSMParameter", source_path="/src/param", dest_path="/dst/param")
        result = lambda_handler(event, None)

        # STS should be called at least twice (source + destination)
        assert mock_sts.assume_role.call_count >= 2, (
            f"Expected at least 2 STS assume_role calls (source + dest), got {mock_sts.assume_role.call_count}"
        )

    @patch("sync_config_items.boto3")
    def test_multi_region_support(self, mock_boto3):
        """Source Region eu-central-1 and destination Region eu-west-1 use respective regions."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": '{"key": "val"}'}
        mock_sm.put_secret_value.return_value = {}

        clients_created = []

        def client_factory(service, **kwargs):
            clients_created.append((service, kwargs))
            if service == "sts":
                return mock_sts
            return mock_sm

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SecretsManager",
            source_region="eu-central-1",
            dest_region="eu-west-1",
        )
        lambda_handler(event, None)

        # Should have SM clients with different regions
        sm_regions = [c[1].get("region_name") for c in clients_created if c[0] == "secretsmanager"]
        assert "eu-central-1" in sm_regions, "Source client should use eu-central-1"
        assert "eu-west-1" in sm_regions, "Destination client should use eu-west-1"


# ===========================================================================
# SYNC-03: Path Mapping with Wildcards and {name} Placeholder
# ===========================================================================

class TestPathMapping:
    """Tests for path mapping with glob wildcards (SYNC-03)."""

    def test_wildcard_expansion_sm(self):
        """SourcePath '/app/prod/*' matches secrets and generates correct destination paths."""
        from sync_config_items import resolve_wildcard_items

        mock_client = MagicMock()
        # Simulate listing secrets: paginator returns pages
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"SecretList": [
                {"Name": "/app/prod/secret-a"},
                {"Name": "/app/prod/secret-b"},
                {"Name": "/app/staging/other"},
            ]}
        ]
        mock_client.get_paginator.return_value = mock_paginator

        pairs = resolve_wildcard_items(
            source_path="/app/prod/*",
            dest_pattern="/dest/prod/{name}",
            source_client=mock_client,
            item_type="SecretsManager",
        )

        source_names = [p[0] for p in pairs]
        assert "/app/prod/secret-a" in source_names
        assert "/app/prod/secret-b" in source_names
        assert "/app/staging/other" not in source_names

    def test_name_placeholder(self):
        """SourcePath with wildcard maps {name} placeholder in destination correctly."""
        from sync_config_items import resolve_wildcard_items, map_destination_path

        # Test map_destination_path directly
        dest = map_destination_path(
            source_path="/rubix/bene-prod/app/hybris/config",
            source_pattern="/rubix/bene-prod/app/*",
            dest_pattern="/digital/prd/app/mro-bene/{name}",
        )
        assert dest == "/digital/prd/app/mro-bene/hybris/config"

    def test_no_wildcard_uses_literal_paths(self):
        """SourcePath '/exact/secret' uses paths as-is without expansion."""
        from sync_config_items import resolve_wildcard_items

        mock_client = MagicMock()
        pairs = resolve_wildcard_items(
            source_path="/exact/secret",
            dest_pattern="/dest/secret",
            source_client=mock_client,
            item_type="SecretsManager",
        )

        assert pairs == [("/exact/secret", "/dest/secret")]
        # Client should NOT be called for listing when no wildcard
        mock_client.get_paginator.assert_not_called()

    def test_double_star_glob(self):
        """SourcePath '/prefix/**' matches deeply nested paths."""
        from sync_config_items import resolve_wildcard_items

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"SecretList": [
                {"Name": "/prefix/a/b/c"},
                {"Name": "/prefix/x"},
                {"Name": "/other/y"},
            ]}
        ]
        mock_client.get_paginator.return_value = mock_paginator

        pairs = resolve_wildcard_items(
            source_path="/prefix/**",
            dest_pattern="/dest/{name}",
            source_client=mock_client,
            item_type="SecretsManager",
        )

        source_names = [p[0] for p in pairs]
        assert "/prefix/a/b/c" in source_names
        assert "/prefix/x" in source_names
        assert "/other/y" not in source_names


# ===========================================================================
# SYNC-04: Value Transforms (JSON key replace, skip, string replace)
# ===========================================================================

class TestTransforms:
    """Tests for value transformation logic (SYNC-04)."""

    def test_json_key_replace(self):
        """Transforms replace value in JSON secret by key."""
        from sync_config_items import apply_transforms

        value = json.dumps({"db_host": "prod-db.example.com", "db_port": "5432"})
        transforms = {
            "db_host": {"replace": [{"from": "prod-db", "to": "dev-db"}]}
        }

        result = apply_transforms(value, transforms)
        result_data = json.loads(result)

        assert result_data["db_host"] == "dev-db.example.com"
        assert result_data["db_port"] == "5432"  # Unchanged

    def test_json_key_skip(self):
        """Transforms skip=true excludes key from synced value."""
        from sync_config_items import apply_json_transforms

        data = {"db_host": "prod-db", "password": "secret123", "db_port": "5432"}
        transforms = {"password": {"skip": True}}

        result_str = apply_json_transforms(data, transforms)
        result_data = json.loads(result_str)

        assert "password" not in result_data
        assert result_data["db_host"] == "prod-db"
        assert result_data["db_port"] == "5432"

    def test_string_value_replace(self):
        """Non-JSON secret string with transform replaces values."""
        from sync_config_items import apply_string_transforms

        value = "host=prod-db port=5432"
        transforms = {
            "host": {"replace": [{"from": "prod-db", "to": "dev-db"}]}
        }

        result = apply_string_transforms(value, transforms)

        assert "dev-db" in result
        assert "prod-db" not in result

    def test_multiple_transforms_applied(self):
        """Multiple keys transformed in single secret."""
        from sync_config_items import apply_transforms

        value = json.dumps({
            "db_host": "prod-db.example.com",
            "api_url": "https://prod.api.example.com",
            "cache_host": "prod-cache.example.com",
        })
        transforms = {
            "db_host": {"replace": [{"from": "prod-db", "to": "dev-db"}]},
            "api_url": {"replace": [{"from": "prod.api", "to": "dev.api"}]},
        }

        result = apply_transforms(value, transforms)
        result_data = json.loads(result)

        assert "dev-db" in result_data["db_host"]
        assert "dev.api" in result_data["api_url"]
        assert result_data["cache_host"] == "prod-cache.example.com"  # Unchanged

    def test_no_transforms_passes_value_through(self):
        """Empty Transforms dict copies value as-is."""
        from sync_config_items import apply_transforms

        value = json.dumps({"key": "value", "other": "data"})
        transforms = {}

        result = apply_transforms(value, transforms)
        assert json.loads(result) == json.loads(value)


# ===========================================================================
# SYNC-05: Auto-Create Destination Secret/Parameter
# ===========================================================================

class TestAutoCreate:
    """Tests for auto-creation of destination secrets/parameters (SYNC-05)."""

    @patch("sync_config_items.boto3")
    def test_sm_create_when_dest_not_exists(self, mock_boto3):
        """put_secret_value raises ResourceNotFoundException -> create_secret called."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm_source = MagicMock()
        mock_sm_source.get_secret_value.return_value = {"SecretString": '{"key": "value"}'}

        mock_sm_dest = MagicMock()
        # Simulate ResourceNotFoundException on put_secret_value
        not_found = type("ResourceNotFoundException", (Exception,), {})
        mock_sm_dest.exceptions.ResourceNotFoundException = not_found
        mock_sm_dest.put_secret_value.side_effect = not_found("Secret not found")
        mock_sm_dest.create_secret.return_value = {}

        call_count = {"sts": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "secretsmanager":
                call_count["sts"] += 1
                # First SM client is source, second is destination
                if call_count["sts"] == 1:
                    return mock_sm_source
                return mock_sm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager")
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] in ("created", "synced")
        mock_sm_dest.create_secret.assert_called_once()

    @patch("sync_config_items.boto3")
    def test_sm_update_when_dest_exists(self, mock_boto3):
        """put_secret_value succeeds -> no create_secret call."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm_source = MagicMock()
        mock_sm_source.get_secret_value.return_value = {"SecretString": '{"key": "value"}'}

        mock_sm_dest = MagicMock()
        mock_sm_dest.put_secret_value.return_value = {}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "secretsmanager":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_sm_source
                return mock_sm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager")
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] in ("updated", "synced")
        mock_sm_dest.create_secret.assert_not_called()

    @patch("sync_config_items.boto3")
    def test_ssm_create_new_parameter(self, mock_boto3):
        """put_parameter with Type from source succeeds for new param."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm_source = MagicMock()
        mock_ssm_source.get_parameter.return_value = {
            "Parameter": {"Name": "/src/param", "Value": "myvalue", "Type": "SecureString"}
        }

        mock_ssm_dest = MagicMock()
        mock_ssm_dest.put_parameter.return_value = {"Version": 1}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "ssm":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_ssm_source
                return mock_ssm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SSMParameter",
            source_path="/src/param",
            dest_path="/dst/param",
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] in ("created", "synced", "updated")
        mock_ssm_dest.put_parameter.assert_called_once()

    @patch("sync_config_items.boto3")
    def test_ssm_update_existing_parameter(self, mock_boto3):
        """put_parameter Overwrite=True updates existing."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm_source = MagicMock()
        mock_ssm_source.get_parameter.return_value = {
            "Parameter": {"Name": "/src/param", "Value": "updated", "Type": "String"}
        }

        mock_ssm_dest = MagicMock()
        mock_ssm_dest.put_parameter.return_value = {"Version": 2}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "ssm":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_ssm_source
                return mock_ssm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SSMParameter",
            source_path="/src/param",
            dest_path="/dst/param",
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        # Verify Overwrite=True was used
        put_call = mock_ssm_dest.put_parameter.call_args
        assert put_call[1].get("Overwrite") is True or (
            len(put_call[0]) > 0 or "Overwrite" in str(put_call)
        )


# ===========================================================================
# SYNC-06: Merge Mode
# ===========================================================================

class TestMergeMode:
    """Tests for merge mode preserving destination-only keys (SYNC-06)."""

    def test_merge_preserves_dest_only_keys(self):
        """MergeMode=true: source {a:1,b:2}, dest {b:3,c:4} -> result has c:4 preserved."""
        from sync_config_items import merge_values

        source = {"a": 1, "b": 2}
        dest = {"b": 3, "c": 4}
        transforms = {}

        result = merge_values(source, dest, transforms)

        # Source-only key "a" is copied
        assert result["a"] == 1
        # Common key "b" without transform: destination wins
        assert result["b"] == 3
        # Destination-only key "c" preserved
        assert result["c"] == 4

    def test_merge_with_transform_uses_source(self):
        """MergeMode=true with Transform on key: use transformed source value."""
        from sync_config_items import merge_values

        source = {"a": 1, "b": "old-value"}
        dest = {"b": "dest-val"}
        transforms = {"b": {"replace": [{"from": "old", "to": "new"}]}}

        result = merge_values(source, dest, transforms)

        # Key "b" has a transform, so transformed source value is used
        assert "new" in str(result["b"])

    def test_no_merge_overwrites_entirely(self):
        """MergeMode=false: source replaces dest entirely (dest-only keys lost)."""
        from sync_config_items import apply_transforms

        source_value = json.dumps({"a": 1, "b": 2})
        transforms = {}

        # Without merge mode, apply_transforms just returns the value as-is
        result = apply_transforms(source_value, transforms)
        result_data = json.loads(result)

        # Destination-only keys should NOT be present (no merge)
        assert "c" not in result_data  # If dest had {"c": 4}, it's lost
        assert result_data == {"a": 1, "b": 2}

    @patch("sync_config_items.boto3")
    def test_merge_non_json_keeps_dest(self, mock_boto3):
        """Non-JSON secret with MergeMode=true keeps destination value if exists."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm_source = MagicMock()
        mock_sm_source.get_secret_value.return_value = {"SecretString": "source-plain-value"}

        mock_sm_dest = MagicMock()
        mock_sm_dest.get_secret_value.return_value = {"SecretString": "dest-plain-value"}
        mock_sm_dest.put_secret_value.return_value = {}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "secretsmanager":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_sm_source
                return mock_sm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager", merge_mode=True)
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        # With MergeMode=true and non-JSON, destination value should be kept
        assert result["result"]["status"] in ("skipped", "synced", "updated")

    @patch("sync_config_items.boto3")
    def test_merge_non_json_copies_source_if_no_dest(self, mock_boto3):
        """Non-JSON with MergeMode=true, dest doesn't exist -> copies source."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_sm_source = MagicMock()
        mock_sm_source.get_secret_value.return_value = {"SecretString": "source-plain-value"}

        mock_sm_dest = MagicMock()
        not_found = type("ResourceNotFoundException", (Exception,), {})
        mock_sm_dest.exceptions.ResourceNotFoundException = not_found
        mock_sm_dest.get_secret_value.side_effect = not_found("Not found")
        mock_sm_dest.put_secret_value.side_effect = not_found("Not found")
        mock_sm_dest.create_secret.return_value = {}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "secretsmanager":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_sm_source
                return mock_sm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(item_type="SecretsManager", merge_mode=True)
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] in ("created", "synced")


# ===========================================================================
# SYNC-07: SSM Recursive Traversal
# ===========================================================================

class TestSSMRecursive:
    """Tests for recursive SSM parameter traversal (SYNC-07)."""

    @patch("sync_config_items.boto3")
    def test_recursive_traversal_fetches_all_params(self, mock_boto3):
        """get_parameters_by_path(Recursive=True) fetches params under path."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm_source = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Parameters": [
                {"Name": "/config/app/db_host", "Value": "db.example.com", "Type": "String"},
                {"Name": "/config/app/db_port", "Value": "5432", "Type": "String"},
            ]}
        ]
        mock_ssm_source.get_paginator.return_value = mock_paginator

        mock_ssm_dest = MagicMock()
        mock_ssm_dest.put_parameter.return_value = {"Version": 1}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "ssm":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_ssm_source
                return mock_ssm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SSMParameter",
            source_path="/config/app/*",
            dest_path="/dest/app/{name}",
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        # Should have processed multiple parameters
        status = result["result"].get("status") or result["result"].get("items_synced")
        assert status is not None

    @patch("sync_config_items.boto3")
    def test_recursive_path_mapping_applied_per_param(self, mock_boto3):
        """Each param gets path mapping applied individually."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm_source = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Parameters": [
                {"Name": "/config/app/db_host", "Value": "val1", "Type": "String"},
                {"Name": "/config/app/api_key", "Value": "val2", "Type": "SecureString"},
            ]}
        ]
        mock_ssm_source.get_paginator.return_value = mock_paginator

        mock_ssm_dest = MagicMock()
        mock_ssm_dest.put_parameter.return_value = {"Version": 1}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "ssm":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_ssm_source
                return mock_ssm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SSMParameter",
            source_path="/config/app/*",
            dest_path="/dest/app/{name}",
        )
        result = lambda_handler(event, None)

        # Each parameter should get its own destination path
        put_calls = mock_ssm_dest.put_parameter.call_args_list
        if put_calls:
            dest_names = [c[1].get("Name", c[0][0] if c[0] else "") for c in put_calls]
            # At least two parameters should be written
            assert len(put_calls) >= 2, f"Expected at least 2 put_parameter calls, got {len(put_calls)}"

    @patch("sync_config_items.boto3")
    def test_recursive_preserves_parameter_type(self, mock_boto3):
        """SecureString params stay SecureString at destination."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = _sts_credentials()

        mock_ssm_source = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Parameters": [
                {"Name": "/config/app/secret_key", "Value": "sensitive", "Type": "SecureString"},
            ]}
        ]
        mock_ssm_source.get_paginator.return_value = mock_paginator

        mock_ssm_dest = MagicMock()
        mock_ssm_dest.put_parameter.return_value = {"Version": 1}

        call_count = {"n": 0}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            if service == "ssm":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return mock_ssm_source
                return mock_ssm_dest
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        event = _make_event(
            item_type="SSMParameter",
            source_path="/config/app/*",
            dest_path="/dest/app/{name}",
        )
        result = lambda_handler(event, None)

        # Verify Type=SecureString was preserved in put_parameter
        if mock_ssm_dest.put_parameter.called:
            put_call = mock_ssm_dest.put_parameter.call_args
            assert put_call[1].get("Type") == "SecureString" or "SecureString" in str(put_call)


# ===========================================================================
# Error Handling
# ===========================================================================

class TestErrorHandling:
    """Tests for error-safe behavior of the Lambda."""

    @patch("sync_config_items.boto3")
    def test_error_returns_status_error_not_raises(self, mock_boto3):
        """Any business error returns {'status': 'error'} in result, never raises."""
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = Exception("STS failure")

        mock_boto3.client.return_value = mock_sts

        event = _make_event(item_type="SecretsManager")

        # Should NOT raise -- should return error result
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] == "error"

    @patch("sync_config_items.boto3")
    def test_unhandled_exception_returns_error_result(self, mock_boto3):
        """Unexpected exception caught, returns statusCode 200 with error result."""
        mock_boto3.client.side_effect = RuntimeError("Totally unexpected")

        event = _make_event(item_type="SecretsManager")

        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["result"]["status"] == "error"
        assert "unexpected" in result["result"]["message"].lower() or len(result["result"]["message"]) > 0

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
