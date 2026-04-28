---
title: "feat: claude-bridge post-Unit-10 follow-ups (deploy hardening + deferred work)"
type: feat
status: active
date: 2026-04-28
parent: docs/plans/2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md
---

# feat: claude-bridge post-Unit-10 follow-ups (deploy hardening + deferred work)

**Target repos:**
- `k8s-argocd` (apps/automation/claude-bridge/, infrastructure/metallb/)
- `claude-bridge` (src/, runbooks/)
- `ansible-quasarlab` (roles/postgresql/, scripts/)

## Context

Unit 10 (k8s-argocd PR #156) shipped the claude-bridge Deployment + LoadBalancer + PreSync migration on 2026-04-27, but the first sync uncovered a chain of latent issues. All blocking issues are resolved and the bridge is healthy on http://192.168.1.235:8080 with 4 slash commands registered to the QuasarLab guild. This plan tracks:

1. The remaining deferred Phase-1 items already noted in the master plan.
2. New follow-ups surfaced during the Unit 10 deploy.
3. Hardening work the team chose to defer to ship the MVP.

## Status of Unit 10 deploy fixes (resolved, recorded for history)

These were closed during the deploy session; no work remaining, listed only so future readers can correlate the chain of PRs.

| Issue | Resolution | PR |
|---|---|---|
| GHCR package was private; cluster could not pull image | Made `mithr4ndir/claude-bridge` GHCR package public | n/a (web UI) |
| Image missing `alembic.ini` so the migration Job's `alembic -c alembic.ini` fell through to a partial config | `COPY alembic.ini /app/alembic.ini` in Dockerfile | claude-bridge#28 |
| `config.set_main_option` ran the env-supplied DSN through ConfigParser BasicInterpolation, which crashed on `%2B`/`%3D` from URL-encoded passwords | Escape `%` to `%%` before `set_main_option` in `env.py` | claude-bridge#29 |
| `dsn` 1P field used `postgresql+psycopg://` scheme; runtime asyncpg only accepts `postgresql://` | Split scheme by field: `dsn` -> `postgresql://` (asyncpg), `migrate_dsn` -> `postgresql+psycopg://` (SQLAlchemy + psycopg3) | 1P content fix only |
| Service had both `metallb.universe.tf/loadBalancerIPs` annotation AND deprecated `spec.loadBalancerIP` -> MetalLB rejected | Drop `spec.loadBalancerIP` | k8s-argocd#159 |
| 192.168.1.227 was already in use by `trading/api-service` and `trading/dashboard-service` (different repo) | Move bridge to 192.168.1.235 | k8s-argocd#160 |
| `externalTrafficPolicy: Local` could not advertise from k8cluster3 because that node's MetalLB speaker is not consistently elected leader | Switch to `externalTrafficPolicy: Cluster` (workaround; see Item 1 below for the durable fix) | k8s-argocd#161 |

## Open Follow-ups

### Item 1: MetalLB memberlist enablement (priority: HIGH)

**Symptom.** All `ServiceL2Status` records cluster-wide show `ALLOCATED NODE: k8cluster1`. Speaker pods on k8cluster2 and k8cluster3 have restart counts of 5 to 7 and have never won an L2 leader election. The startup log shows:

```
"not starting fast dead node detection (memberlist),
 need ml-bindaddr / ml-labels config"
```

Without memberlist, MetalLB's L2 leader election is deterministic and k8cluster1 always wins. This works for `externalTrafficPolicy: Cluster` services (kube-proxy SNATs traffic across nodes) but breaks `Local` because the LB IP is announced ONLY from the node that has the pod, and only k8cluster1's speaker can announce.

**Impact.**
- claude-bridge had to switch to `Cluster`, losing client source-IP preservation in audit logs.
- Any future service that needs client-IP preservation in this cluster would hit the same wall.
- Single point of failure: if k8cluster1 goes down, every LB IP becomes unreachable until manual recovery.

**Fix.**
1. Update `infrastructure/metallb/values.yaml` (or the rendered chart) so the speaker DaemonSet sets `--ml-bindaddr=$(METALLB_HOST)` and `--ml-secret-key-path=/etc/metallb/memberlist-key` (the standard memberlist config from the MetalLB chart values `speaker.memberlist.enabled: true`).
2. Generate a 128-bit memberlist key, store it in 1Password under `Infrastructure / metallb-memberlist`, materialize via ESO into the `metallb-system` namespace as `Secret/memberlist`.
3. Re-render and commit; verify all 3 speakers join the cluster (`memberlist join` log lines).
4. Confirm `ServiceL2Status` records start distributing across nodes (existing services with `ETP: Cluster` may flip leaders; that is expected and harmless).
5. Re-run the bridge with `ETP: Local` to validate (Item 2).

**Acceptance.**
- All 3 speakers report memberlist convergence.
- At least one service ends up with `ALLOCATED NODE` other than k8cluster1.
- claude-bridge with `ETP: Local` advertises and serves probes from 192.168.1.235.
- Restart counts on speaker pods stop growing (sample over 24 hours).

### Item 2: Restore `externalTrafficPolicy: Local` on claude-bridge

Blocked on Item 1. Reverts k8s-argocd#161. Restores client source-IP in the bridge's request logs. Update the comment in `service.yaml` to reflect that memberlist is now in place.

### Item 3: ServiceMonitor + `/metrics` endpoint (priority: MEDIUM)

The Unit 10 PR description noted this as deferred. Bridge currently exposes `/healthz` and `/readyz` only.

1. Add `prometheus_fastapi_instrumentator` (or equivalent) to the bridge, exposing default HTTP metrics + custom counters for:
   - `bridge_actions_issued_total{tier}` (POST /notify)
   - `bridge_actions_resolved_total{tier, decision}` (slash command outcomes)
   - `bridge_token_verify_failures_total{reason}`
   - `bridge_allowlist_denials_total`
   - `bridge_hmac_secret_slot_used_total{slot}` (current vs previous, for rotation telemetry)
2. Mount under `/metrics` with the existing `claude-bridge-secrets` API key gate, OR a separate `metrics_token` so Prometheus can scrape without holding a notify-capable key.
3. Add `apps/automation/claude-bridge/servicemonitor.yaml` matching the discord-alert-proxy pattern (release: kube-prometheus-stack label).
4. Verify the metrics show up in Prometheus + a basic Grafana dashboard panel.

### Item 4: Application-level alerts (priority: MEDIUM)

Blocked on Item 3 (the rules need real metrics, not infrastructure ones).

Add to `apps/automation/claude-bridge/prometheus-rules.yaml`:

- `ClaudeBridgeAllowlistAnomaly` -- single allowlisted user issues more than 5 `/approve` decisions in a 10-minute window. Per the master plan, this is the Discord account takeover signal. Severity: critical.
- `ClaudeBridgeApprovalRateSpike` -- bridge is issuing more than N tokens/minute (compare 5 min rolling vs 1 hour baseline). Severity: warning.
- `ClaudeBridgeHmacRotationStale` -- `bridge_hmac_secret_slot_used_total{slot="previous"}` is non-zero for more than 16 minutes after a rotation (rotation overlap should be exactly 15 min). Severity: warning.
- `ClaudeBridgeRemoteAgentTier3Denied` -- spike in 403s from `remote-agent-*` keys hitting Tier 3, signal of a misconfigured caller. Severity: info.

### Item 5: TLS termination at the LB edge (priority: MEDIUM)

Today the bridge is HTTP only over the trusted LAN (matches `discord-alert-proxy`). Hooks pass `X-API-Key` in plaintext over the LAN. Fine for the homelab threat model, but a known gap.

Options:
- **A.** Route through an existing nginx-ingress (or Traefik) instance with cert-manager-managed certs from a private CA. Requires a DNS name like `claude-bridge.quasarlab.local`.
- **B.** Self-signed cert mounted into the bridge pod, enabled via uvicorn `--ssl-certfile`/`--ssl-keyfile`. Smaller blast radius but no automatic renewal.
- **C.** Upgrade to mTLS with client certs from `cert-manager`, hooks present a cert instead of API keys (long-term, deeper change).

Recommendation: A, deferred until cert-manager is in place. Track separately if this stretches beyond a week of investigation.

### Item 6: Workstation `~/.claude/hooks/.env` operator-flip (priority: LOW, manual)

Until the operator updates their workstation env, hooks still resolve to `command-center1.lan:8080` and the bridge stays unused. Required values:

```
CLAUDE_BRIDGE_URL=http://192.168.1.235:8080
CLAUDE_BRIDGE_HOOK=polling
```

After the first end-to-end Tier 3 approval cycle works, rotate `CLAUDE_BRIDGE_API_KEY` (regenerate, write to `claude-bridge/api-key-local-hook` 1P item, force ESO sync). Document in `claude-bridge/runbooks/local-hook-rotation.md`.

### Item 7: First end-to-end smoke test in Discord (priority: LOW, manual)

Trigger a deny-listed Bash command from Claude Code (with hook=polling). Confirm:
- Embed appears in `#bot-approvals`.
- `/approve <action_id>` from the allowlisted Discord user (`130532855058137089`) returns success.
- `/deny <action_id>` test from a non-allowlisted Discord user returns 403-equivalent ephemeral reply.
- Audit log row exists in Postgres `audit_log` table for both attempts.
- Hook unblocks the original Bash call after `/approve`.

If any of these fail, capture `kubectl -n automation logs deploy/claude-bridge` + the audit log row and open an issue.

### Item 8: 1P-to-DSN generator helper (priority: LOW)

The 1P-generated PG passwords are URL-unsafe (contain `+`, `/`, `=`). The bridge handles this (env.py %-escape, separate `dsn` and `migrate_dsn` fields with different schemes), but the workaround is implicit. Make it explicit:

1. Add `claude-bridge/runbooks/postgres-dsn-rebuild.md` documenting the URL-encoding requirement and the scheme split (asyncpg vs SQLAlchemy).
2. Optionally: a small Python helper in `scripts/` that reads the PG password 1P items, builds both DSNs (URL-encoded passwords, correct schemes), and writes them to the `claude-bridge/postgres-dsn` 1P item. Idempotent. Use after every PG password rotation so DSNs cannot drift from passwords.

### Item 9: Multi-replica with leader election (priority: LOWEST, do-not-do-yet)

Master plan documents this as deferred until measured demand. Single replica + PDB + `priorityClassName: system-cluster-critical` is the documented Phase-1 design because two Gateway-connected replicas would double-fire interaction events. Note here purely so the deferral is visible alongside the active follow-ups; do not pick this up without a clear reason.

## Sequencing

```
Item 1 (MetalLB memberlist)
   |
   +--> Item 2 (restore ETP: Local)

Item 3 (/metrics + ServiceMonitor)
   |
   +--> Item 4 (app-level alerts)

Item 5 (TLS) -- independent

Item 6 (operator .env flip) -- independent, manual, do first
Item 7 (E2E smoke test)      -- independent, manual, do after Item 6
Item 8 (DSN runbook+helper)  -- independent
Item 9 -- not on critical path
```

## Out of scope

- Bridge feature work (Tier 2 outbox worker, redaction layer, Unit 11 portfolio entry, Unit 12 ESO circuit breaker). Those are tracked in the master plan and the umbrella issue.
- General MetalLB upgrade. Item 1 is the minimal change to enable memberlist; broader upgrade work belongs in its own ticket.
- Migration of `discord-alert-proxy` or other LB services to `ETP: Local`. Same memberlist gating applies but only call it out if the team chooses to do that work.
