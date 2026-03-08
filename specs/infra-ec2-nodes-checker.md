# Spec: EC2/K8s Nodes Checker

## Identifiant
- **ID**: `infra-ec2-nodes`
- **Domaine**: infrastructure
- **Priorite**: P1
- **Scope**: `CLUSTER` (par cluster EKS)

## Objectif

Verifier l'etat de sante des nodes K8s (instances EC2) :
- Statut EC2 (running, status checks)
- Conditions K8s des nodes (Ready, Pressure)
- Etat Karpenter (NodePools, NodeClaims)
- Correlation EC2 <-> K8s node

## Architecture

### Composants

```
                    Step Function: infra-ec2-nodes-checker
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │  EC2 Describe   │   │  EKS: Get Nodes │   │ EKS: Karpenter  │
    │   Instances     │   │   (eks:call)    │   │  (eks:call)     │
    │  (aws-sdk)      │   │                 │   │                 │
    └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────┐
                    │   Lambda: process-ec2-nodes         │
                    │   - Correlate EC2 <-> K8s nodes     │
                    │   - Check conditions                │
                    │   - Determine health status         │
                    └─────────────────┬───────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │          DynamoDB State             │
                    └─────────────────────────────────────┘
```

## Checks a implementer

### 1. EC2 Instance Status

| Check | Status | Source | Critere |
|-------|--------|--------|---------|
| `ec2_instance_state` | ok/critical | EC2 API | Toutes instances "running" |
| `ec2_status_checks` | ok/warning/critical | EC2 API | system + instance status = "ok" |
| `ec2_impaired` | ok/critical | EC2 API | Pas d'instance impaired |

**API**: `ec2:DescribeInstances` + `ec2:DescribeInstanceStatus`

### 2. K8s Node Conditions

| Check | Status | Source | Critere |
|-------|--------|--------|---------|
| `node_ready` | ok/critical | K8s API | Tous nodes Ready=True |
| `node_memory_pressure` | ok/warning | K8s API | MemoryPressure=False |
| `node_disk_pressure` | ok/warning | K8s API | DiskPressure=False |
| `node_pid_pressure` | ok/warning | K8s API | PIDPressure=False |
| `node_network` | ok/critical | K8s API | NetworkUnavailable=False |
| `node_cordoned` | ok/warning | K8s API | spec.unschedulable=false |

**API**: `GET /api/v1/nodes`

### 3. Karpenter Status (si deploye)

| Check | Status | Source | Critere |
|-------|--------|--------|---------|
| `nodepool_ready` | ok/warning | K8s API | NodePools actifs |
| `nodeclaim_provisioned` | ok/warning | K8s API | NodeClaims provisioned |
| `nodeclaim_disrupting` | ok/info | K8s API | NodeClaims en disruption |

**API**: `GET /apis/karpenter.sh/v1/nodepools`, `GET /apis/karpenter.sh/v1/nodeclaims`

### 4. Correlation EC2 <-> K8s

| Check | Status | Source | Critere |
|-------|--------|--------|---------|
| `orphan_ec2` | ok/warning | Correlation | Pas d'EC2 sans node K8s correspondant |
| `orphan_node` | ok/warning | Correlation | Pas de node K8s sans EC2 correspondant |

## Input

```json
{
  "Project": "string - projet (ex: mro-mi2)",
  "Env": "string - environnement (ex: nh-stg)",
  "ClusterName": "string - nom du cluster EKS",
  "CrossAccountRoleArn": "string - ARN du role cross-account",
  "IncludeKarpenter": "boolean - inclure checks Karpenter (default: true)",
  "NodeFilters": {
    "tags": {"kubernetes.io/cluster/{cluster}": "owned"},
    "labelSelector": "node.kubernetes.io/instance-type"
  }
}
```

## DynamoDB Keys

- **pk**: `{Project}#{Env}` (ex: `mro-mi2#nh-stg`)
- **sk**: `check:infra:ec2-nodes:current`

## Output

```json
{
  "component": "ec2-nodes",
  "status": "ok|warning|critical",
  "healthy": true|false,
  "summary": {
    "ec2_instances": 5,
    "ec2_running": 5,
    "ec2_status_ok": 5,
    "k8s_nodes": 5,
    "k8s_ready": 5,
    "karpenter_nodepools": 2,
    "karpenter_nodeclaims": 5
  },
  "checks": [
    {
      "name": "ec2_instance_state",
      "status": "ok",
      "message": "All 5 instances running",
      "details": {
        "instances": [
          {"id": "i-xxx", "state": "running", "type": "m6i.xlarge", "az": "eu-central-1a"}
        ]
      }
    },
    {
      "name": "node_ready",
      "status": "ok",
      "message": "All 5 nodes Ready",
      "details": {
        "nodes": [
          {"name": "ip-10-0-1-x", "ready": true, "instance_id": "i-xxx"}
        ]
      }
    }
  ],
  "correlation": {
    "matched": 5,
    "orphan_ec2": [],
    "orphan_nodes": []
  },
  "timestamp": "2026-01-12T..."
}
```

## Filtrage des instances EC2

Les instances sont filtrees par tags pour ne recuperer que les nodes du cluster :

```python
filters = [
    {"Name": "tag:kubernetes.io/cluster/{cluster_name}", "Values": ["owned", "shared"]},
    {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]}
]
```

## Correlation EC2 <-> K8s Node

La correlation utilise le `ProviderID` du node K8s :
```
spec.providerID: aws:///eu-central-1a/i-0123456789abcdef0
```

Extraction: `i-0123456789abcdef0`

## Tests

### Environnement de test

- **Cluster**: `k8s-dig-stg-webshop` (NH staging)
- **Account**: 281127105461
- **CrossAccountRoleArn**: `arn:aws:iam::281127105461:role/iam-dig-stg-refresh-destination-role`

### Commande de test

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:eu-central-1:025922408720:stateMachine:ops-dashboard-infra-ec2-nodes-checker \
  --input '{
    "Project": "mro-mi2",
    "Env": "nh-stg",
    "ClusterName": "k8s-dig-stg-webshop",
    "CrossAccountRoleArn": "arn:aws:iam::281127105461:role/iam-dig-stg-refresh-destination-role",
    "IncludeKarpenter": true
  }' \
  --profile shared-services/AWSAdministratorAccess --region eu-central-1
```

## Implementation

### Phase 1 (MVP)
- [x] Spec document
- [ ] Step Function ASL
- [ ] Lambda process-ec2-nodes
- [ ] Terraform resources
- [ ] Test on staging

### Phase 2
- [ ] CloudWatch metrics (CPU, memory)
- [ ] ASG health check (managed node groups)
- [ ] Compare step function (legacy vs NH)
