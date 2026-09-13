# Tasks Document

- [ ] 1. Audit current ExternalSecret refresh intervals across the cluster
  - File: none (investigation), output recorded inline in this task comment once run
  - Run `kubectl get externalsecrets -A -o json | jq '.items[] | {ns:.metadata.namespace, name:.metadata.name, refresh:.spec.refreshInterval}'`
  - Compute projected daily reads: sum(86400 / refreshInterval_seconds) per secret; note anything under 24h
  - Purpose: Establish the baseline fleet-wide read rate before changing anything
  - _Leverage: existing 1P quota metrics on Grafana to cross-check observed vs projected_
  - _Requirements: 1.4_

- [ ] 2. Standardize refresh intervals to 24h with documented exceptions
  - Files: `infrastructure/**/externalsecret.yaml`, `apps/**/externalsecret.yaml`
  - Set `spec.refreshInterval: 24h` on every ExternalSecret unless rotation SLO dictates otherwise
  - Add inline YAML comment for any non-24h value explaining why
  - Purpose: Collapse fleet steady-state reads to ~1 per secret per day
  - _Leverage: existing 1P feedback memory `feedback_eso_sync_after_1p_change.md` 24h precedent_
  - _Requirements: 1.1, 1.2, 1.3_

- [ ] 3. Stagger refresh times into an overnight window
  - Files: `infrastructure/**/externalsecret.yaml`, `apps/**/externalsecret.yaml`
  - Add `spec.refreshPolicy: Periodic` and `spec.refreshInterval: 24h` anchored so refreshes cluster into the 02:00-03:00 US/Pacific window (use a small spread per namespace to avoid a single-minute thundering herd)
  - Note that ESO v2 may not support absolute schedule anchoring; if so, document the "best-effort distribution" alternative and keep the staggering loose
  - Purpose: Concentrate reads in a known quiet window, produce a visible quota heartbeat
  - _Leverage: ESO v2.1.0 docs (currently deployed image `ghcr.io/external-secrets/external-secrets:v2.1.0`)_
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 4. Add CI check that blocks ExternalSecrets missing refreshInterval
  - File: `.github/workflows/externalsecret-lint.yml` (new) or extend the existing kustomize-build workflow
  - Script: `yq` or `kustomize build | grep` over every ExternalSecret manifest, fail if `spec.refreshInterval` is absent, empty, or `0s`
  - Purpose: Prevent regression via future PRs
  - _Leverage: existing GitHub Actions in `.github/workflows/`_
  - _Requirements: 1.5_

- [ ] 5. Implement ESO circuit breaker (preferred path: CronJob + kubectl)
  - Files:
    - `infrastructure/external-secrets/circuit-breaker/cronjob.yaml` (new)
    - `infrastructure/external-secrets/circuit-breaker/rbac.yaml` (new, ServiceAccount + Role + RoleBinding scoped to `external-secrets` namespace, verbs `get`/`patch` on `deployments/scale`)
    - `infrastructure/external-secrets/circuit-breaker/script-configmap.yaml` (new, holds the trip/recover shell script)
    - `infrastructure/external-secrets/kustomization.yaml` (update to include the directory)
  - Schedule: every 2 minutes (`*/2 * * * *`)
  - Logic: query Prometheus for `onepassword_ratelimit_remaining{type="account"}` via the in-cluster Prometheus service; if less than 10, `kubectl -n external-secrets scale deploy external-secrets --replicas=0`; if 100 or more and currently 0, scale to 1. Default-closed if metric is unavailable.
  - Purpose: Automate the manual mitigation from the 2026-04-18 runbook
  - _Leverage: existing kube-prometheus-stack service DNS name, existing Discord webhook pattern from monitoring alerts_
  - _Requirements: 2.1, 2.2, 2.5, NFR security (RBAC scope)_

- [ ] 6. Tell ArgoCD to ignore the ESO replicas field
  - File: `bootstrap/apps/external-secrets.yaml` (or equivalent ArgoCD Application manifest for ESO)
  - Add `spec.ignoreDifferences: [{group: apps, kind: Deployment, jsonPointers: ["/spec/replicas"]}]`
  - Purpose: Let the circuit breaker win over ArgoCD reconciliation during a trip
  - _Leverage: existing ArgoCD Application shape used for HA-managed workloads_
  - _Requirements: 2.4_

- [ ] 7. Add Discord alerts for circuit-breaker trips
  - File: `infrastructure/monitoring/kube-prometheus-stack/values.yaml` (new alert rule group `eso-circuit-breaker`)
  - Alerts:
    - `ESOCircuitBreakerTripped` (critical, fires when ESO replicas=0 AND remaining<10)
    - `ESOCircuitBreakerStuck` (warning, fires when replicas=0 AND remaining>=500 for 10m, indicates recovery logic bug)
    - `ESOCircuitBreakerHeartbeatStale` (warning, fires if last_evaluation metric >10m old)
  - Themed message: LOTR. Suggested: "The Beacons of Gondor are lit (ESO paused, quota exhausted)"
  - Purpose: Visibility on automated trips
  - _Leverage: existing PrometheusRule group `onepassword-quota` already shipped in this repo_
  - _Requirements: 2.3, NFR usability_

- [ ] 8. Add a circuit-breaker state panel to the 1P Grafana dashboard
  - File: `infrastructure/monitoring/grafana-dashboards/grafana-dashboard-1password-quota.yaml`
  - New single-stat panel, binary "ESO breaker: CLOSED/OPEN" driven off `kube_deployment_spec_replicas{namespace="external-secrets",deployment="external-secrets"}`
  - Purpose: Operator sees breaker state at the same place they see quota
  - _Leverage: existing dashboard panel shape_
  - _Requirements: NFR usability_

- [ ] 9. Investigate ClusterSecretStore validation cost
  - File: none (investigation)
  - Read ESO v2.1.0 source for the ClusterSecretStore health-check loop, confirm whether each reconcile does an `op` vault-item call or only a control-plane call
  - If vault-item call: open upstream issue, file a follow-up task to downgrade to ConfigMap-driven token injection
  - If control-plane only: note as not-a-real-cost, close Requirement 3 with a decision record
  - Purpose: Validate or invalidate a key assumption from the 2026-04-18 incident
  - _Leverage: ESO docs + source_
  - _Requirements: 3.1_

- [ ] 10. Update runbook and CLAUDE.md
  - Files:
    - `docs/runbooks/onepassword-quota.md` (update: remove manual scale-to-0, add circuit-breaker diagnostic)
    - `CLAUDE.md` (k8s-argocd root, add a brief note: "ESO scaling is managed by the circuit breaker, do not kubectl scale manually")
  - Purpose: Align written procedure with automated reality
  - _Leverage: existing runbook_
  - _Requirements: 5.1, 5.2, 5.3_

- [ ] 11. End-to-end smoke test
  - Set up a synthetic condition: scrape a fake quota metric exposing `onepassword_ratelimit_remaining=5`, confirm CronJob trips ESO to 0 within 4 minutes, confirm alert fires, confirm Grafana panel flips to OPEN; flip metric back to 950, confirm recovery to 1 within 4 minutes
  - Record results in PR description
  - Purpose: Verify the feature actually works end-to-end before trusting it in production
  - _Leverage: existing prometheus stack, node_exporter textfile collector pattern_
  - _Requirements: all_
