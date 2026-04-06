#!/usr/bin/env bash
#
# run-validation.sh -- Wrapper to validate the lsdb Dask cluster.
#
# Sets up a kubectl port-forward to the Dask scheduler, activates the
# lsdb venv, runs the validation script, and cleans up.
#
# Usage: ./run-validation.sh [SCHEDULER_ADDRESS]
#
# If SCHEDULER_ADDRESS is provided, skip port-forward setup and connect
# directly (useful for in-cluster runs or existing port-forwards).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LSDB_VENV="${LSDB_VENV:-$HOME/code/lsdb/.venv}"
NAMESPACE="science"
SERVICE="svc/lsdb-cluster-scheduler"
LOCAL_PORT="${LOCAL_PORT:-8786}"
REMOTE_PORT="8786"
PF_PID=""

cleanup() {
    if [[ -n "${PF_PID}" ]]; then
        echo "Cleaning up port-forward (PID ${PF_PID})..."
        kill "${PF_PID}" 2>/dev/null || true
        wait "${PF_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# If a scheduler address is provided, skip port-forward
SCHEDULER="${1:-}"

if [[ -z "${SCHEDULER}" ]]; then
    echo "Starting port-forward: ${SERVICE} ${LOCAL_PORT}:${REMOTE_PORT} (ns: ${NAMESPACE})"
    kubectl port-forward -n "${NAMESPACE}" "${SERVICE}" "${LOCAL_PORT}:${REMOTE_PORT}" &
    PF_PID=$!

    # Wait for port-forward to become ready (up to 15 seconds)
    echo "Waiting for port-forward to be ready..."
    for i in $(seq 1 30); do
        if bash -c "echo >/dev/tcp/localhost/${LOCAL_PORT}" 2>/dev/null; then
            echo "Port-forward ready."
            break
        fi
        if ! kill -0 "${PF_PID}" 2>/dev/null; then
            echo "ERROR: port-forward process died. Is the Dask scheduler running?"
            exit 1
        fi
        sleep 0.5
    done

    SCHEDULER="localhost:${LOCAL_PORT}"
fi

# Activate lsdb virtual environment
if [[ ! -d "${LSDB_VENV}" ]]; then
    echo "ERROR: lsdb venv not found at ${LSDB_VENV}"
    echo "Set LSDB_VENV to the path of your lsdb virtual environment."
    exit 1
fi

echo "Activating venv: ${LSDB_VENV}"
# shellcheck disable=SC1091
source "${LSDB_VENV}/bin/activate"

echo ""
python3 "${SCRIPT_DIR}/validate-lsdb.py" "${SCHEDULER}"
exit_code=$?

exit ${exit_code}
