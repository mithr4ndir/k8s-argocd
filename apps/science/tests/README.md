# LSDB Dask Cluster Validation

Smoke tests for the lsdb + Dask stack running in the `science` namespace on Kubernetes.

## Prerequisites

- `kubectl` configured with cluster access
- Python virtual environment with `lsdb` and `distributed` installed
  - Default path: `~/code/lsdb/.venv` (override with `LSDB_VENV` env var)
- Dask cluster running in the `science` namespace

## Quick Start

From the command center (192.168.1.88):

```bash
cd ~/code/k8s-argocd/apps/science/tests
./run-validation.sh
```

The wrapper script handles port-forwarding, venv activation, and cleanup automatically.

## Manual Run

If you already have a port-forward or want to connect directly:

```bash
# Set up port-forward yourself
kubectl port-forward -n science svc/lsdb-cluster-scheduler 8786:8786 &

# Activate venv and run
source ~/code/lsdb/.venv/bin/activate
python3 validate-lsdb.py localhost:8786
```

For in-cluster execution (e.g., from a debug pod):

```bash
python3 validate-lsdb.py lsdb-cluster-scheduler.science.svc.cluster.local:8786
```

## Configuration

| Variable     | Default              | Description                         |
|--------------|----------------------|-------------------------------------|
| `LSDB_VENV`  | `~/code/lsdb/.venv` | Path to lsdb Python virtual env    |
| `LOCAL_PORT` | `8786`               | Local port for kubectl port-forward |

## What It Tests

| # | Test                        | Validates                                        |
|---|-----------------------------|-------------------------------------------------|
| 1 | Connect to Dask scheduler   | Scheduler reachable, workers registered          |
| 2 | Generate synthetic catalog  | lsdb.generate_catalog works on Dask              |
| 3 | Compute full catalog        | Distributed compute returns 10,000 rows          |
| 4 | Cone search                 | Spatial cone query (RA=180, DEC=0, r=10 deg)     |
| 5 | Box search                  | Spatial box query (RA 0-90, DEC -45 to 45)       |
| 6 | Crossmatch                  | Cross-catalog matching with radius constraint    |
| 7 | Write to HATS format        | Catalog serialization to HATS parquet output     |

## Example Output

```
LSDB Dask Cluster Validation
Scheduler: tcp://localhost:8786
============================================================
  PASS  Connect to Dask scheduler (0.12s) -- 1 worker(s), 2 thread(s), 4.0 GB memory
  PASS  Generate synthetic catalog (0.45s) -- Generated catalog with n_order=3, seed=42
  PASS  Compute full catalog (10k rows) (2.31s) -- 10000 rows computed
  PASS  Cone search (RA=180, DEC=0, r=10) (1.87s) -- 75 objects within 10 deg of (RA=180, DEC=0)
  PASS  Box search (RA 0-90, DEC -45..45) (1.64s) -- 1818 objects in RA[0,90] DEC[-45,45]
  PASS  Crossmatch two catalogs (3.42s) -- 9 matches (radius=2 arcsec)
  PASS  Write catalog to HATS format (1.23s) -- Wrote 12 file(s) to /tmp/lsdb_validate_.../test_catalog
============================================================
Result: 7/7 passed (11.04s total) -- ALL PASSED
```

## When to Run

- After deploying or upgrading the Dask cluster
- After upgrading lsdb or its dependencies
- After changes to the HATS catalogs PVC or NFS storage
- After Kubernetes node changes affecting the `science` namespace
- As a periodic health check
