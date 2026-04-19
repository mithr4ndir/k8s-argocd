# Tasks Document

Status update 2026-04-18: tasks 1-4 (ansible-quasarlab collector) shipped as PR #109 (merged `8637a3a`). Tasks 5-8 (this repo) previously had stale `[x]` marks without any artifacts on disk. Now actually delivered on branch `feat/1password-quota-monitoring`: alert rules + runbook + standalone Grafana dashboard + wiring. Task 9 (end-to-end smoke) remains pending until both PRs are deployed.

- [ ] 1. Create op-quota-parse.py in ansible-quasarlab/roles/op_quota_collector/files/
  - File: ansible-quasarlab/roles/op_quota_collector/files/op-quota-parse.py
  - Parse `op service-account ratelimit` stdin, emit Prometheus text on stdout
  - Handle "N/A" reset (omit line), "5 hours from now" style, "23 hours and 59 minutes" style
  - Python 3 stdlib only (re, sys, time)
  - Purpose: Convert CLI table output into metric format
  - _Leverage: None (new helper, no existing parsers)_
  - _Requirements: 1.5_

- [ ] 2. Create op-quota-collector.sh template
  - File: ansible-quasarlab/roles/op_quota_collector/templates/op-quota-collector.sh.j2
  - Source scripts/lib/op-killswitch.sh, call op_killswitch_check_or_exit
  - Load OP_SERVICE_ACCOUNT_TOKEN from ~/.config/op/service-account-token
  - Capture op stderr, scan via op_killswitch_scan_file on failure
  - Always exit 0, emit onepassword_ratelimit_collector_success on every path
  - Atomic textfile rename
  - Purpose: Orchestrate collection lifecycle
  - _Leverage: ansible-quasarlab/scripts/lib/op-killswitch.sh_
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [ ] 3. Create systemd service + timer templates
  - Files:
    - ansible-quasarlab/roles/op_quota_collector/templates/op-quota-collector.service.j2
    - ansible-quasarlab/roles/op_quota_collector/templates/op-quota-collector.timer.j2
  - Service: Type=oneshot, User=ladino, runs /usr/local/bin/op-quota-collector.sh
  - Timer: OnCalendar=*:0/15 (every 15m), Persistent=true
  - Purpose: Scheduled execution surviving reboots
  - _Leverage: existing etcd-defrag.service/timer shape in roles/k8s_maintenance/templates/_
  - _Requirements: 2.1, 2.3_

- [ ] 4. Wire role into cmd_center.yml playbook + add parser tests
  - Files:
    - ansible-quasarlab/roles/op_quota_collector/tasks/main.yml (deploy script + parser + units, daemon-reload, enable timer)
    - ansible-quasarlab/roles/op_quota_collector/defaults/main.yml (interval, paths)
    - ansible-quasarlab/roles/op_quota_collector/tests/fixtures/*.txt (sample op outputs)
    - ansible-quasarlab/roles/op_quota_collector/tests/test_parse.py (pytest assertions)
    - ansible-quasarlab/playbooks/cmd_center.yml (add role)
  - Purpose: Complete the ansible-side deployment + parser regression coverage
  - _Leverage: roles/k8s_maintenance/tasks/main.yml as the shape reference_
  - _Requirements: 1.1-1.5, 2.1-2.3_

- [x] 5. Add PrometheusRule group in kube-prometheus-stack values.yaml
  - File: k8s-argocd/infrastructure/monitoring/kube-prometheus-stack/values.yaml
  - New `onepassword-quota` group under `additionalPrometheusRulesMap`
  - 5 alerts: OnePasswordQuotaHalfConsumed (info/50%/15m), Low (warning/20%/10m), Critical (critical/5%/5m), Exhausted (critical/0%/0m), CollectorStale (warning/30m)
  - Each alert has runbook_url annotation pointing to docs/runbooks/onepassword-quota.md
  - Purpose: Graduated severity matching urgency
  - _Leverage: existing etcd-health group in the same file_
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 6. Create runbook at docs/runbooks/onepassword-quota.md
  - File: k8s-argocd/docs/runbooks/onepassword-quota.md
  - Sections: what triggered the alert, `op service-account ratelimit` command to confirm, top 3 actions (trip kill switch, scale ESO to 0, identify biggest consumer via op-collector metrics)
  - Include wait-time guidance for hourly vs daily limits
  - Purpose: On-call has immediate next steps
  - _Leverage: existing runbook pattern (if any) in the repo_
  - _Requirements: 3.2 (linked runbook), NFR usability_

- [x] 7. Add Grafana dashboard for 1Password quota (standalone, uid `onepassword-quota`)
  - File: k8s-argocd/infrastructure/monitoring/grafana-dashboards/grafana-dashboard-1password-quota.yaml
  - 7 panels: 3 gauges (account daily, token hourly read, token hourly write), 3 stat (time-to-reset, collector health, collector age), 1 24h time-series with threshold lines at 500/800/950
  - Wired via kustomization.yaml entry for infrastructure/monitoring/grafana-dashboards/
  - Decision: standalone dashboard instead of panels grafted onto an existing overview, because the 1P cap is a distinct subsystem with its own on-call flow (runbook linked from alerts); mixing into quasarlab-overview dilutes the signal
  - Purpose: At-a-glance quota visibility
  - _Leverage: grafana-dashboard-ansible-ansible-runs.yaml as the ConfigMap shape reference_
  - _Requirements: 4.1, 4.2, 4.3_

- [x] 8. PR in k8s-argocd with rules + runbook + Grafana panel
  - Open PR referencing this spec
  - Title: "feat(monitoring): 1Password quota alerts, runbook, Grafana panel"
  - Note in PR body that the collector (tasks 1-4) lives in ansible-quasarlab and is tracked in that repo's spec workflow
  - Purpose: Ship the monitoring side independently of the collector deployment
  - _Leverage: recent monitoring PRs (#132 etcd alerts) as the PR shape_
  - _Requirements: all (this is the delivery vehicle)_

- [~] 9. End-to-end smoke test after both repos' PRs merge (partial, blocked on collector deployment)
  - [x] ArgoCD dev app adopted the Grafana dashboard ConfigMap cleanly
  - [x] PrometheusRule groups `onepassword-quota` (7 alerts) and `external-secrets-health` (2 alerts) deployed and visible in Prometheus UI
  - [x] 3 ExternalSecrets ServiceMonitors deployed via chart `serviceMonitor.enabled=true, renderMode=alwaysRender`
  - [ ] Live `onepassword_ratelimit_*` metrics in Prometheus. Blocked on ansible-quasarlab deploying the op-ratelimit-collector via `cmd_center.yml --tags op_ratelimit_collector`. Will not land until 1P cap recovers (stuck at ~100/1000 remaining on 2026-04-19 after an ansible retry storm).
  - [ ] Threshold-flip Discord smoke test. Can run immediately (rule deploys independent of metric data) but deferred until real metric flow so the alert fires on authentic condition, not synthetic.
  - [ ] `OnePasswordQuotaCollectorStale` for-30m test. Requires collector deployed + running briefly, then kill-switch tripped.
  - _Leverage: kubectl exec pattern already used for other monitoring validation_
  - _Requirements: all acceptance criteria, integration testing per design doc_

- [ ] 10. 2026-04-19 follow-up: second round of alerts for ESO retry-loop detection (shipped in same PR #140)
  - [x] ExternalSecretNotReady (warning, 10m on `externalsecret_status_condition{Ready,False}==1`)
  - [x] ExternalSecretSyncErrorBurst (warning, 5m on `increase(externalsecret_sync_calls_error[15m]) > 3`)
  - [x] Runbook extended with ExternalSecret Retry Alerts section (triage, root-cause table, reconcile-paused containment)
  - [x] Per-SA hourly token tier alerts added: OnePasswordTokenReadHourlyLow (<200/1000) and OnePasswordTokenWriteHourlyLow (<20/100)
  - _Leverage: ESO chart native ServiceMonitor, existing runbook_
  - _Requirements: defense-in-depth on the incident class that bit us on 2026-04-18_

- [x] 11. 2026-04-19 learning: confirm `op service-account ratelimit` is a free control-plane call
  - Previous assumption: every ratelimit probe costs 1 read, hence collector 15m cadence (96/day = 9.6% of 1000 cap)
  - Verified against a draining cap: USED counter stayed flat across back-to-back probes
  - Implication: tighten collector cadence to 5m (PR #121 in ansible-quasarlab); remove "costs a read" warnings from runbook
  - Auto-memory updated at `~/.claude/projects/-home-ladino/memory/feedback_op_rate_limit_care.md`
