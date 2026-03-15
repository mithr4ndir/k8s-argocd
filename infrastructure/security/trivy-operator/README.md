# Trivy Operator

Automated container image vulnerability scanning for all workloads in the K8s cluster.

## What It Does
- Scans all container images for CRITICAL and HIGH CVEs
- Ignores unfixed vulnerabilities to reduce noise
- Creates `VulnerabilityReport` CRDs per workload
- Exposes metrics via ServiceMonitor for Prometheus scraping

## Verify
```bash
kubectl get vulnerabilityreports -A
```
