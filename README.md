# Tenant Observer Operator

A Kubernetes operator built with [Kopf](https://kopf.readthedocs.io/) that monitors tenant resource usage across namespaces and exposes insights via custom `TenantInfo` resources.

## Overview

Tenant Observer watches namespaces annotated with tenant identifiers resource and aggregates consumption data, including:

- **Compute Resources**: CPU requests/limits, memory requests/limits
- **Pod Status**: Running, pending, and failed pod counts
- **Services**: Number of services per tenant
- **Health Scoring**: Tenant health based on resource utilization and pod health

All data is exposed through `TenantInfo` custom resources, making it easily accessible via standard kubectl commands and suitable for monitoring and alerting.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Tenant Observer Operator                      │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Namespace    │───▶│ Tenant       │───▶│ TenantInfo       │  │
│  │ Watcher      │    │ Aggregator   │    │ CRD              │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
│         │                  │                     │              │
│         ▼                  ▼                     ▼              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ RBAC         │    │ Resource     │    │ Grafana          │  │
│  │ Permissions  │    │ Calculator   │    │ Dashboard        │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Kubernetes cluster (v1.19+)
- kubectl configured with cluster access
- Docker (for building the operator image)
- Python 3.9+

## Quick Start

### 1. Install CRD

```bash
kubectl apply -f crd/tenantinfo.yaml
```

### 2. Set up RBAC

```bash
kubectl apply -f k8s/rbac.yaml
```

### 3. Build and Deploy

```bash
# Build the Docker image
docker build -t tenant-observer:0.1 .

# Deploy to Kubernetes
kubectl apply -f k8s/deployment.yaml
```

### 4. Verify Installation

```bash
# Check operator status
kubectl get pods -n tenant-observer

# View TenantInfo resources
kubectl get tenantinfos
```

## Configuration

### Namespace Annotation

Mark namespaces with tenant identifiers:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: team-alpha
  labels:
    tenant: alpha-team
```

The operator watches for the `tenant` label on namespaces.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPERATOR_NAME` | Name of the operator deployment | `tenant-observer` |
| `NAMESPACE` | Namespace where operator runs | `tenant-observer` |

## Usage

### Viewing Tenant Information

```bash
# List all tenants with resource summary
kubectl get tenantinfos

# Detailed view with all columns
kubectl get tenantinfos -o wide

# JSON format for automation
kubectl get tenantinfos -o json
```

### Sample Output

```
NAME         TENANT    NAMESPACES   CPU REQ   CPU %   MEM REQ   MEM %   RUNNING   PENDING   FAILED   SERVICES   HEALTH   STATUS   AGE
alpha-info   alpha     team-alpha   2000m     45      4Gi       60      10        0         0        5          92      Ready    5d
beta-info    beta      team-beta    1000m     20      2Gi       30      5         1         0        2          78      Ready    5d
```

### Accessing Detailed Information

```bash
kubectl get tenantinfo alpha-info -o yaml
```

Returns a resource containing:
- Tenant name and associated namespaces
- CPU requests and limits with percentages
- Memory requests and limits with percentages
- Pod counts by status (running, pending, failed)
- Service count
- Health score and status

## Files Structure

```
tenant-observer/
├── README.md                    # This file
├── observer-operator.py         # Main operator code
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container image build
├── crd/
│   └── tenantinfo.yaml          # TenantInfo CRD definition
├── k8s/
│   ├── deployment.yaml          # Operator deployment & service
│   ├── rbac.yaml                # RBAC permissions
│   └── prometheus-alerts.yaml   # Prometheus alert rules
└── grafana/
    └── tenant-observer-dashboard.json  # Grafana dashboard
```

## Monitoring

### Prometheus Alerts

Apply the included alert rules:

```bash
kubectl apply -f k8s/prometheus-alerts.yaml
```

### Grafana Dashboard

Import the dashboard from `grafana/tenant-observer-dashboard.json` for visual monitoring of tenant resource usage.

## Development

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run operator locally (requires kubeconfig)
python observer-operator.py
```

### Building Custom Image

```bash
docker build -t registry.example.com/tenant-observer:v1.0 .
docker push registry.example.com/tenant-observer:v1.0
```

Update `k8s/deployment.yaml` with your image path.

## RBAC Permissions

The operator requires the following permissions:

### ClusterRole

| Resource | Verbs |
|----------|-------|
| `customresourcedefinitions` | get, list, watch |
| `namespaces` | get, list, watch |
| `tenants` | get, list, watch |
| `deployments` | get, list, watch |
| `statefulsets` | get, list, watch |
| `pods` | get, list, watch |
| `nodes` | get, list, watch |

### Role (tenant-observer namespace)

| Resource | Verbs |
|----------|-------|
| `events` | create, patch |
| `secrets` | get, list, watch |

## Troubleshooting

### Operator Not Starting

Check pod logs:
```bash
kubectl logs -n tenant-observer -l app=tenant-observer
```

### Permission Warnings

If you see warnings about insufficient permissions:
```bash
kubectl auth can-i list pods --as=system:serviceaccount:tenant-observer:tenant-observer
```

Ensure RBAC is correctly applied:
```bash
kubectl apply -f k8s/rbac.yaml
```

### No TenantInfo Resources

1. Verify namespaces have the `tenant` label
2. Check operator is watching the correct namespaces
3. Review operator logs for aggregation errors

## License

This project is licensed under the MIT License.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request
