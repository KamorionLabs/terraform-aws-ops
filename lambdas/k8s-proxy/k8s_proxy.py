"""
Lambda function acting as a proxy for Kubernetes API calls via the EKS private endpoint.

Replaces Step Functions eks:call and eks:runJob.sync integrations when the EKS
cluster's public endpoint has restricted CIDR whitelist. The Lambda runs in the
destination account VPC, so it can reach the EKS private endpoint directly.

Supported actions:
  - call:         Generic K8s API call (replaces eks:call)
  - createJob:    Create a K8s Job (first part of eks:runJob.sync)
  - getJobStatus: Poll Job completion status
  - deleteJob:    Delete a K8s Job (cleanup)

Used by Step Functions: ManageStorage, ScaleServices, RunMysqldumpOnEks, RunMysqlimportOnEks
"""

import base64
import boto3
import json
import logging
import os
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from botocore.signers import RequestSigner


# Configure logging
logger = logging.getLogger()
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logger.setLevel(getattr(logging, log_level))

# Token validity (seconds)
STS_TOKEN_EXPIRES_IN = 60

# Module-level cache for cluster info (endpoint + CA cert)
_cluster_cache = {}


def get_cluster_info(cluster_name: str, region: str) -> dict:
    """
    Get EKS cluster endpoint and CA certificate, with caching.

    Args:
        cluster_name: EKS cluster name
        region: AWS region

    Returns:
        Dict with 'endpoint' and 'ca_data' (base64-decoded CA cert bytes)
    """
    cache_key = f"{region}/{cluster_name}"
    if cache_key in _cluster_cache:
        logger.debug(f"Using cached cluster info for {cache_key}")
        return _cluster_cache[cache_key]

    logger.info(f"Describing EKS cluster: {cluster_name} in {region}")
    eks_client = boto3.client("eks", region_name=region)
    response = eks_client.describe_cluster(name=cluster_name)

    cluster = response["cluster"]
    info = {
        "endpoint": cluster["endpoint"],
        "ca_data": base64.b64decode(cluster["certificateAuthority"]["data"]),
    }

    _cluster_cache[cache_key] = info
    logger.info(f"Cluster endpoint: {info['endpoint']}")
    return info


def get_bearer_token(cluster_name: str, region: str) -> str:
    """
    Generate a bearer token for EKS authentication using STS presigned URL.

    Args:
        cluster_name: EKS cluster name (used as x-k8s-aws-id header)
        region: AWS region

    Returns:
        Bearer token string (k8s-aws-v1.<base64url-encoded-presigned-url>)
    """
    session = boto3.Session()
    sts_client = session.client("sts", region_name=region)
    service_id = sts_client.meta.service_model.service_id

    signer = RequestSigner(
        service_id, region, "sts", "v4",
        session.get_credentials(), session.events
    )

    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }

    signed_url = signer.generate_presigned_url(
        params, region_name=region,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=""
    )

    base64_url = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("utf-8")
    return "k8s-aws-v1." + base64_url.rstrip("=")


def _build_ssl_context(ca_data: bytes) -> ssl.SSLContext:
    """
    Build an SSL context with the cluster CA certificate.

    Args:
        ca_data: Decoded CA certificate bytes

    Returns:
        Configured ssl.SSLContext
    """
    ctx = ssl.create_default_context()

    # Write CA cert to a temp file for load_verify_locations
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as ca_file:
        ca_file.write(ca_data)
        ca_path = ca_file.name

    try:
        ctx.load_verify_locations(ca_path)
    finally:
        os.unlink(ca_path)

    return ctx


def _get_content_type(method: str, content_type: str = None) -> str:
    """
    Return the appropriate Content-Type header for the HTTP method.

    Args:
        method: HTTP method (GET, POST, PATCH, PUT, DELETE)
        content_type: Optional override (e.g. "application/merge-patch+json" for CRDs)

    Returns:
        Content-Type string
    """
    if content_type:
        return content_type
    if method == "PATCH":
        return "application/strategic-merge-patch+json"
    return "application/json"


def k8s_api_call(cluster_name: str, region: str, method: str, path: str,
                 body: dict = None, query_params: dict = None,
                 content_type: str = None) -> dict:
    """
    Make an authenticated call to the Kubernetes API.

    Args:
        cluster_name: EKS cluster name
        region: AWS region
        method: HTTP method
        path: K8s API path (e.g. /api/v1/namespaces/default/pods)
        body: Request body (optional)
        query_params: URL query parameters (optional)

    Returns:
        Dict with 'ResponseBody' and 'StatusCode'

    Raises:
        K8sApiError: On HTTP errors (status >= 400)
    """
    cluster_info = get_cluster_info(cluster_name, region)
    token = get_bearer_token(cluster_name, region)
    ssl_ctx = _build_ssl_context(cluster_info["ca_data"])

    # Build URL
    url = f"{cluster_info['endpoint']}{path}"
    if query_params:
        url += "?" + urllib.parse.urlencode(query_params)

    # Build request
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", _get_content_type(method, content_type))
    req.add_header("Accept", "application/json")

    logger.info(f"K8s API call: {method} {path}")

    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
            response_body = json.loads(resp.read().decode("utf-8"))
            return {
                "ResponseBody": response_body,
                "StatusCode": resp.status,
            }
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass

        logger.error(f"K8s API error: {e.code} {method} {path} - {error_body}")
        raise K8sApiError(e.code, method, path, error_body)


class K8sApiError(Exception):
    """Exception raised for K8s API HTTP errors."""

    def __init__(self, status_code: int, method: str, path: str, body: str):
        self.status_code = status_code
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"K8s API error {status_code}: {method} {path} - {body}")


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def handle_call(event: dict, region: str) -> dict:
    """
    Handle the 'call' action - generic K8s API call.

    Args:
        event: Lambda event with ClusterName, Method, Path, RequestBody, QueryParameters
        region: AWS region

    Returns:
        Dict with ResponseBody and StatusCode
    """
    cluster_name = event["ClusterName"]
    method = event["Method"].upper()
    path = event["Path"]
    body = event.get("RequestBody")
    query_params = event.get("QueryParameters")
    content_type = event.get("ContentType")

    result = k8s_api_call(cluster_name, region, method, path, body, query_params, content_type)
    logger.info(f"Call succeeded: {result['StatusCode']}")
    return result


def handle_create_job(event: dict, region: str) -> dict:
    """
    Handle the 'createJob' action - create a K8s Job.

    Args:
        event: Lambda event with ClusterName, Namespace, Job
        region: AWS region

    Returns:
        Dict with JobName, Namespace, ClusterName, Status
    """
    cluster_name = event["ClusterName"]
    namespace = event["Namespace"]
    job_spec = event["Job"]

    path = f"/apis/batch/v1/namespaces/{namespace}/jobs"

    result = k8s_api_call(cluster_name, region, "POST", path, body=job_spec)
    job_name = result["ResponseBody"]["metadata"]["name"]

    logger.info(f"Job created: {job_name} in {namespace}")

    return {
        "JobName": job_name,
        "Namespace": namespace,
        "ClusterName": cluster_name,
        "Status": "Created",
    }


def handle_get_job_status(event: dict, region: str) -> dict:
    """
    Handle the 'getJobStatus' action - poll Job completion.

    Args:
        event: Lambda event with ClusterName, Namespace, JobName
        region: AWS region

    Returns:
        Dict with JobName, Status (Running|Complete|Failed), Message
    """
    cluster_name = event["ClusterName"]
    namespace = event["Namespace"]
    job_name = event["JobName"]

    path = f"/apis/batch/v1/namespaces/{namespace}/jobs/{job_name}"

    result = k8s_api_call(cluster_name, region, "GET", path)
    job = result["ResponseBody"]

    status = "Running"
    message = ""

    conditions = job.get("status", {}).get("conditions", [])
    for condition in conditions:
        if condition.get("type") == "Complete" and condition.get("status") == "True":
            status = "Complete"
            message = condition.get("message", "Job completed successfully")
            break
        if condition.get("type") == "Failed" and condition.get("status") == "True":
            status = "Failed"
            message = condition.get("message", "Job failed")
            break

    # Also check succeeded/failed counts as fallback
    if status == "Running":
        succeeded = job.get("status", {}).get("succeeded", 0)
        failed = job.get("status", {}).get("failed", 0)
        if succeeded and succeeded > 0:
            status = "Complete"
            message = f"Job completed with {succeeded} succeeded pod(s)"
        elif failed and failed > 0:
            status = "Failed"
            message = f"Job failed with {failed} failed pod(s)"

    logger.info(f"Job {job_name} status: {status}")

    return {
        "JobName": job_name,
        "Namespace": namespace,
        "ClusterName": cluster_name,
        "Status": status,
        "Message": message,
    }


def handle_delete_job(event: dict, region: str) -> dict:
    """
    Handle the 'deleteJob' action - delete a K8s Job and its pods.

    Args:
        event: Lambda event with ClusterName, Namespace, JobName
        region: AWS region

    Returns:
        Dict with JobName, Status
    """
    cluster_name = event["ClusterName"]
    namespace = event["Namespace"]
    job_name = event["JobName"]

    path = f"/apis/batch/v1/namespaces/{namespace}/jobs/{job_name}"

    # propagationPolicy=Background ensures pods are cleaned up
    query_params = {"propagationPolicy": "Background"}

    try:
        k8s_api_call(cluster_name, region, "DELETE", path, query_params=query_params)
        logger.info(f"Job deleted: {job_name}")
    except K8sApiError as e:
        if e.status_code == 404:
            logger.info(f"Job {job_name} already deleted (404), ignoring")
        else:
            raise

    return {
        "JobName": job_name,
        "Namespace": namespace,
        "ClusterName": cluster_name,
        "Status": "Deleted",
    }


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

# Action dispatch table
_ACTION_HANDLERS = {
    "call": handle_call,
    "createJob": handle_create_job,
    "getJobStatus": handle_get_job_status,
    "deleteJob": handle_delete_job,
}


def lambda_handler(event, context):
    """
    Lambda handler for K8s API proxy.

    Expected event structure:
    {
        "Action": "call|createJob|getJobStatus|deleteJob",
        "ClusterName": "k8s-dig-stg-webshop",
        ...action-specific fields...
    }
    """
    region = os.environ.get("AWS_REGION", "eu-central-1")

    action = event.get("Action")
    if not action:
        raise ValueError("Missing required field: Action")

    handler = _ACTION_HANDLERS.get(action)
    if not handler:
        raise ValueError(f"Unknown action: {action}. Valid actions: {list(_ACTION_HANDLERS.keys())}")

    cluster_name = event.get("ClusterName")
    if not cluster_name:
        raise ValueError("Missing required field: ClusterName")

    logger.info(f"Processing action: {action} on cluster: {cluster_name}")

    try:
        return handler(event, region)
    except K8sApiError as e:
        # Re-raise with a message that includes the status code for Step Functions error matching
        raise RuntimeError(f"K8sApiError.{e.status_code}: {e}") from e
