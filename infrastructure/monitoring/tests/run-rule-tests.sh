#!/usr/bin/env bash
# Run promtool unit tests against alert rules that live inside
# kube-prometheus-stack/values.yaml.
#
#   ./run-rule-tests.sh                     run every *-test.yml here
#   ./run-rule-tests.sh agent-cmd-audit     run one
#
# A test file is named <rules-map key>-test.yml and refers to its rules as
# rules.yml; this script extracts that key out of values.yaml into a temporary
# rules.yml so promtool sees a plain rule group file.
#
# Why these exist: an alert expression that returns nothing today looks exactly
# like a correct one. Every test here asserts the rule FIRES on the failure it
# is meant to catch, so a typo in a metric name fails the run instead of
# shipping a rule that can never fire.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALUES="${HERE}/../kube-prometheus-stack/values.yaml"
IMAGE="${PROMTOOL_IMAGE:-prom/prometheus:v3.5.0}"

command -v docker >/dev/null || { echo "docker is required to run promtool" >&2; exit 1; }
[[ -r "$VALUES" ]] || { echo "cannot read ${VALUES}" >&2; exit 1; }

if (( $# )); then
    keys=("$@")
else
    keys=()
    for f in "${HERE}"/*-test.yml; do
        [[ -e "$f" ]] || continue
        base="$(basename "$f")"
        keys+=("${base%-test.yml}")
    done
fi
(( ${#keys[@]} )) || { echo "no test files found in ${HERE}" >&2; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
# The image runs as nobody, and mktemp -d is 0700, so the mount would be
# unreadable inside the container.
chmod 0755 "$workdir"

status=0
for key in "${keys[@]}"; do
    test_file="${HERE}/${key}-test.yml"
    [[ -r "$test_file" ]] || { echo "no test file for ${key}" >&2; status=1; continue; }

    python3 - "$VALUES" "${key}-alerts" "${workdir}/rules.yml" <<'PY'
import sys, yaml
values, key, out = sys.argv[1], sys.argv[2], sys.argv[3]
tree = yaml.safe_load(open(values))["kube-prometheus-stack"]
rules = tree["additionalPrometheusRulesMap"][key]
with open(out, "w") as handle:
    yaml.safe_dump(rules, handle, sort_keys=False, width=10000)
PY
    cp "$test_file" "${workdir}/test.yml"
    chmod 0644 "${workdir}/test.yml" "${workdir}/rules.yml"

    echo "== ${key}"
    docker run --rm --entrypoint promtool \
        -v "${workdir}:/w:ro" -w /w "$IMAGE" test rules test.yml || status=1
done
exit "$status"
