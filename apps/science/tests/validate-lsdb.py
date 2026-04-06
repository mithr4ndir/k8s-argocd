#!/usr/bin/env python3
"""
LSDB + Dask Cluster Validation Script

Smoke tests for the lsdb stack running on the Kubernetes Dask cluster.
Validates connectivity, catalog operations, spatial queries, crossmatch,
and HATS I/O. Designed for post-deployment verification.

Usage:
    python validate-lsdb.py [SCHEDULER_ADDRESS]

    SCHEDULER_ADDRESS defaults to localhost:8786 (use with port-forward).
    For in-cluster: python validate-lsdb.py lsdb-cluster-scheduler.science.svc.cluster.local:8786
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, List, Tuple


def timed_test(name: str, func: Callable[[], Any]) -> Tuple[bool, str, float]:
    """Run a test function, return (passed, message, elapsed_seconds)."""
    start = time.monotonic()
    try:
        result_msg = func()
        elapsed = time.monotonic() - start
        msg = result_msg if result_msg else "OK"
        print(f"  PASS  {name} ({elapsed:.2f}s) -- {msg}")
        return True, msg, elapsed
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"  FAIL  {name} ({elapsed:.2f}s) -- {exc}")
        return False, str(exc), elapsed


def test_connect(scheduler: str) -> str:
    """Connect to Dask scheduler and verify at least one worker is available."""
    from distributed import Client

    client = Client(scheduler, timeout="30s")
    try:
        info = client.scheduler_info()
        n_workers = len(info.get("workers", {}))
        if n_workers == 0:
            raise RuntimeError("Scheduler reachable but 0 workers registered")
        total_threads = sum(
            w.get("nthreads", 0) for w in info["workers"].values()
        )
        total_memory_gb = sum(
            w.get("memory_limit", 0) for w in info["workers"].values()
        ) / (1024 ** 3)
        return (
            f"{n_workers} worker(s), {total_threads} thread(s), "
            f"{total_memory_gb:.1f} GB memory"
        )
    finally:
        client.close()


def test_generate_catalog(scheduler: str) -> str:
    """Generate a synthetic catalog with 10k objects across 3 orders."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    try:
        catalog = lsdb.generate_catalog(n_objects=10000, n_order=3, seed=42)
        if catalog is None:
            raise RuntimeError("generate_catalog returned None")
        return "Generated catalog with n_order=3, seed=42"
    finally:
        client.close()


def test_compute_catalog(scheduler: str) -> str:
    """Compute the full synthetic catalog and verify row count."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    try:
        catalog = lsdb.generate_catalog(n_objects=10000, n_order=3, seed=42)
        df = catalog.compute()
        row_count = len(df)
        if row_count != 10000:
            raise RuntimeError(
                f"Expected 10000 rows, got {row_count}"
            )
        return f"{row_count} rows computed"
    finally:
        client.close()


def test_cone_search(scheduler: str) -> str:
    """Run a cone search (RA=180, DEC=0, radius=10 deg) and verify results."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    try:
        catalog = lsdb.generate_catalog(n_objects=10000, n_order=3, seed=42)
        result = catalog.cone_search(ra=180, dec=0, radius=10)
        df = result.compute()
        count = len(df)
        if count == 0:
            raise RuntimeError("Cone search returned 0 results")
        return f"{count} objects within 10 deg of (RA=180, DEC=0)"
    finally:
        client.close()


def test_box_search(scheduler: str) -> str:
    """Run a box search (RA 0-90, DEC -45 to 45) and verify results."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    try:
        catalog = lsdb.generate_catalog(n_objects=10000, n_order=3, seed=42)
        result = catalog.box_search(
            ra=(0, 90), dec=(-45, 45)
        )
        df = result.compute()
        count = len(df)
        if count == 0:
            raise RuntimeError("Box search returned 0 results")
        return f"{count} objects in RA[0,90] DEC[-45,45]"
    finally:
        client.close()


def test_crossmatch(scheduler: str) -> str:
    """Crossmatch two synthetic catalogs and verify matches found."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    try:
        cat_a = lsdb.generate_catalog(n_objects=5000, n_order=3, seed=42)
        cat_b = lsdb.generate_catalog(n_objects=5000, n_order=3, seed=99)
        xmatch = cat_a.crossmatch(cat_b, n_neighbors=1, radius_arcsec=2)
        df = xmatch.compute()
        count = len(df)
        if count == 0:
            raise RuntimeError("Crossmatch returned 0 matches")
        return f"{count} matches (radius=2 arcsec)"
    finally:
        client.close()


def test_write_hats(scheduler: str) -> str:
    """Write a catalog to HATS format in a temp directory, verify output."""
    from distributed import Client
    import lsdb

    client = Client(scheduler, timeout="30s")
    tmp_dir = tempfile.mkdtemp(prefix="lsdb_validate_")
    try:
        catalog = lsdb.generate_catalog(n_objects=1000, n_order=2, seed=42)
        output_path = os.path.join(tmp_dir, "test_catalog")
        catalog.to_hats(output_path)

        # Verify output directory has content
        if not os.path.isdir(output_path):
            raise RuntimeError(f"Output directory not created: {output_path}")
        files = []
        for root, _dirs, filenames in os.walk(output_path):
            files.extend(filenames)
        if len(files) == 0:
            raise RuntimeError("HATS output directory is empty")
        return f"Wrote {len(files)} file(s) to {output_path}"
    finally:
        client.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate lsdb + Dask cluster deployment"
    )
    parser.add_argument(
        "scheduler",
        nargs="?",
        default="localhost:8786",
        help="Dask scheduler address (default: localhost:8786)",
    )
    args = parser.parse_args()

    scheduler = args.scheduler
    if not scheduler.startswith("tcp://"):
        scheduler = f"tcp://{scheduler}"

    print("LSDB Dask Cluster Validation")
    print(f"Scheduler: {scheduler}")
    print("=" * 60)

    tests: List[Tuple[str, Callable[[], str]]] = [
        ("Connect to Dask scheduler", lambda: test_connect(scheduler)),
        ("Generate synthetic catalog", lambda: test_generate_catalog(scheduler)),
        ("Compute full catalog (10k rows)", lambda: test_compute_catalog(scheduler)),
        ("Cone search (RA=180, DEC=0, r=10)", lambda: test_cone_search(scheduler)),
        ("Box search (RA 0-90, DEC -45..45)", lambda: test_box_search(scheduler)),
        ("Crossmatch two catalogs", lambda: test_crossmatch(scheduler)),
        ("Write catalog to HATS format", lambda: test_write_hats(scheduler)),
    ]

    results: List[Tuple[str, bool, str, float]] = []
    for name, func in tests:
        passed, msg, elapsed = timed_test(name, func)
        results.append((name, passed, msg, elapsed))

    print("=" * 60)
    passed_count = sum(1 for _, p, _, _ in results if p)
    total = len(results)
    total_time = sum(e for _, _, _, e in results)
    status = "ALL PASSED" if passed_count == total else "SOME FAILED"
    print(f"Result: {passed_count}/{total} passed ({total_time:.2f}s total) -- {status}")

    if passed_count < total:
        print("\nFailed tests:")
        for name, passed, msg, _ in results:
            if not passed:
                print(f"  - {name}: {msg}")

    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
