---
title: "feat: Discord-mediated HITL approval bridge for Claude Code agents (claude-bridge)"
type: feat
status: active
date: 2026-04-26
deepened: 2026-04-26
---

# feat: Discord-mediated HITL approval bridge for Claude Code agents (claude-bridge)

**Target repos:**
- New: `claude-bridge` (FastAPI service, Dockerfile, CI, runbooks). Suggested location: `/home/ladino/code/claude-bridge/`.
- Existing: `k8s-argocd` (manifests at `apps/automation/claude-bridge/`, ServiceMonitor and PrometheusRule, group registration).
- Existing: local hooks at `~/.claude/hooks/` and `~/.claude/settings.json`.

## Plan Revision Notes (2026-04-26 deepening pass)

This plan was deepened on 2026-04-26 after parallel reviews (security, feasibility, adversarial, scope, coherence). Material changes from the v1 draft:

**Cuts (scope discipline):**
- Hash-chained audit log replaced with append-only table + nightly GPG-signed `pg_dump` export. Hash chain was theatre against the actual threat model and racy under multi-replica writes.
- Per-action-class UNDO registry collapsed to inline `if/elif` in `notify.py`. Four hard-coded cases do not need a module.
- Per-channel Discord token-bucket queue removed. discord.py handles 429s natively; single-user homelab traffic does not approach burst limits.
- `observability/tracing.py` placeholder dropped. No tracing requirement is stated.
- Portfolio skeleton removed from Unit 11. Belongs in `quasarlab-portfolio`'s backlog at +30 days, not in this plan.
- Unit 12 (ESO circuit-breaker retrofit) demoted to Deferred-to-Separate-Tasks. It was always gated on k8s-argocd#148 and is tracked in the umbrella issue.
- Redaction layer (Unit 4) moved to Phase 1.5 hardening, not Phase 1. Tightens MVP without losing the security control.

**Corrections (factual):**
- TimescaleDB Postgres VM is `192.168.1.123` (verified via `infrastructure/monitoring/kube-prometheus-stack/values.yaml`). All previous "122 or 123" references resolved.
- ApplicationSet at `bootstrap/applicationset.yaml` generates per-environment, NOT per-app. New app groups MUST be added to `environments/dev/kustomization.yaml` (Unit 10 already does this; Context & Research wording corrected).
- CNI is Calico (per repo runbooks). Standard Kubernetes NetworkPolicy is enforced. Calico FQDN policies are CRD-based and not currently used in the cluster; document the discord.com egress as IP-range based with periodic review, not FQDN.
- Schema PK named `action_id` (not `id`) for clarity across the API surface and documentation.

**Additions (security and operational rigor):**
- API key scope separation: distinct keys for local hooks (`local-hook-*`, all tiers) and remote scheduled agents (`remote-agent-*`, Tier 1 and Tier 2 only). Tier 3 from a remote agent is rejected at the API layer. Audit log captures `actor_source`.
- Postgres role least privilege: `claude_bridge_app` role has `INSERT` and `SELECT` on `audit_log`, no `UPDATE`/`DELETE`/`TRUNCATE`. Append-only is enforced at the role level, not just by trigger. Migrations run as a separate role only during deploy.
- Cluster-down recovery: Phase 1 polling hook honors the same `CLAUDE_BRIDGE_EMERGENCY_OVERRIDE` env var as the Phase 0 hard-block hook, with the same audit semantics. The fail-closed default is preserved; the override exists for the documented PVE-outage class of incident where the bridge itself is unreachable.
- Anomaly detection on the allowlist: PrometheusRule fires when an allowlisted user issues more than 5 approvals in 10 minutes (Discord account takeover signal).
- CODEOWNERS plus branch-protection rule on `apps/automation/claude-bridge/`, `bootstrap/`, and `environments/dev/kustomization.yaml`. Required reviewers enforce the bootstrap-exemption assumption.
- Single replica with PDB and `priorityClassName: system-cluster-critical`, NOT 2 replicas. Both Gateway-connected replicas would receive the same `interaction_create` event and one's deferred response would time out. Single replica is the simplest correct answer for the slash-command flow. Multi-replica with leader election deferred until measured demand justifies it.
- Phase 0 emergency override delivery: passphrase rotated by a small bridge-side cron job that updates the 1P item, accessed by the operator via the 1Password mobile app at incident time, set as a per-invocation env var (NEVER in shell rc files). Documented explicitly in `break-glass.md`.

**Timeline corrections:**
- Phase 0: 1 day (was "afternoon"). Realistic for shell-quoting deny-list defense.
- Phase 1: 3 to 5 days (was "1 to 2"). MVP scope honest about FastAPI + discord.py + Postgres + hooks integration.
- Phase 1.5 (new): 1 day for redaction hardening.
- Phase 2: 1 day. Phase 3: half day. Phase 4: half day.

End of revision notes.

## Overview

Build a Discord-mediated, three-tier human-in-the-loop bridge that lets autonomous Claude Code agents (local interactive sessions and scheduled remote tasks) request approval for actions of varying blast radius. Read-only sweeps (Tier 1) post and proceed. Reversible actions (Tier 2) post a proposal and auto-proceed after a timeout, with a daily digest and undo path. Irreversible actions (Tier 3) are hard-gated, never auto-proceed, and require an explicit slash-command approval from the legitimate user. Local Claude Code `PreToolUse` hooks intercept dangerous Bash commands and poll the bridge for an approval token before allowing execution. Outbound notifications are scrubbed by a regex-based redaction layer with fail-closed semantics. Phase 0 ships a hard-block hook so dangerous commands cannot run unattended even before the bridge is online.

## Problem Frame

The user wants to move from reactive incident-response use of Claude toward genuinely autonomous and scheduled agents (Anthropic Remote Tasks, `/loop`, `/schedule`). That shift is unsafe today: any agent that can run `terraform apply`, `kubectl delete`, `git push --force`, `op item delete`, or destructive Ansible playbooks against the QuasarLab homelab can cause data loss without a human in the loop. Discord is already the user's alerting surface (themed embeds via the existing `discord-alert-proxy` in `infrastructure/monitoring/discord-alert-proxy/`), is mobile-friendly, and supports authenticated slash commands with cryptographic identity binding. A bridge that mediates approvals through Discord turns a brittle "Claude prompts on the laptop only" model into "agents work autonomously, dangerous actions stop at a human gate the user can clear from anywhere".

The bridge is itself security-critical infrastructure: it gates blast-radius operations, runs in the cluster it gates, and handles credentials. It must follow the same security-first defense-in-depth posture mandated by `~/.claude/CLAUDE.md` and the lab's IaC prime directive.

## Requirements Trace

- **R1.** Three-tier risk model with separate Discord channels (`bot-activity`, `bot-proposals`, `bot-approvals`). Tier semantics: Tier 1 observes and proceeds, Tier 2 default-proceeds after a timeout, Tier 3 never auto-proceeds.
- **R2.** Tier 3 approvals require explicit slash-command (`/approve <token>` or `/deny <token>`) from a Discord user ID on a server-side allowlist. Token is HMAC-bound to action, requester, and expiry. Single-shot, ≥128-bit entropy, ≤15-min TTL.
- **R3.** Local `PreToolUse` hook gates a defined set of dangerous commands (`terraform apply`, `kubectl delete`, `git push --force`, `op item delete`, `ansible-playbook --tags destroy`, etc.) and fails closed when the bridge is unreachable, returns a non-approved status, or times out.
- **R4.** Outbound payloads to Discord pass through a redaction layer that fails closed (suppresses send, posts to ops channel) on detected secrets: 1Password tokens, AWS/GCP/Azure keys, JWTs, GitHub PATs, private keys, internal IPs.
- **R5.** Audit log captures approver Discord user ID, timestamp, action hash, decision, and outcome for every Tier 2 and Tier 3 event. Append-only, hash-chained, 90-day retention minimum.
- **R6.** Daily 06:00 digest of auto-proceeded Tier 2 actions in `bot-activity`, each with an `UNDO-<n>` token that, when invoked via slash command, executes a per-action-class undo through the Tier 3 flow.
- **R7.** Bridge deployed to K8s cluster via ArgoCD using existing GitOps patterns from `discord-alert-proxy`. State persisted in the existing PostgreSQL (TimescaleDB VM, 192.168.1.123).
- **R8.** Secrets sourced via existing ESO + 1Password ClusterSecretStore at `infrastructure/external-secrets/cluster-secret-store.yaml`. New ExternalSecrets coordinate with the in-flight ESO circuit-breaker work (k8s-argocd#147 / #148) and proceed in parallel.
- **R9.** Bridge is fail-closed at every layer: hook fails closed when bridge unreachable, redaction fails closed on match, Tier 3 never auto-proceeds, ArgoCD bootstrap and cluster-recovery paths are explicitly exempted with a documented break-glass procedure.
- **R10.** All artifacts managed in code: claude-bridge repo for service, k8s-argocd for manifests, ESO for secrets, no manual configuration.

## Scope Boundaries

In scope:
- Three-tier Discord bridge service (FastAPI + discord.py 2.6, Python 3.12, single Postgres-backed state).
- Slash-command approval UX (`/approve`, `/deny`, `/status`, `/undo`).
- Local `PreToolUse` and `PostToolUse` hooks gating a defined deny list.
- Outbound regex-based redaction layer with ops-channel surfacing on block.
- Audit log with hash-chaining and exportable rows.
- Tier 2 outbox pattern, auto-proceed scheduler, and daily digest cron.
- Per-action-class UNDO contract registry (Kubernetes scale, ArgoCD app sync, file revert) for the actions that have a defined inverse.
- ServiceMonitor, PrometheusRule, Grafana dashboard, runbook.
- Bootstrap exemption list (argocd ns, claude-bridge itself), break-glass procedure documented.

Out of scope:
- Slack or other notification channels. Discord only.
- Web UI. Approvals happen in Discord exclusively.
- Multi-user collaborative approvals (n-of-m signing). Single-user allowlist now.
- Cross-cluster federation. One bridge, one cluster.
- Pre-approval policies (e.g., automatically approve known-safe `terraform apply` patterns). Future work, see Future Considerations.
- HTTP Interactions endpoint mode. Gateway-only.
- Hosting the bridge outside the cluster as a non-K8s fallback. See break-glass section for the Phase 0 emergency override; a true off-cluster fallback is deferred.

### Deferred to Separate Tasks

- **ESO circuit-breaker integration**: The bridge's ExternalSecrets are added in parallel with k8s-argocd#147/#148 (project_2026-04-22_secrets_iac_rollout). Once #148 lands, follow-up unit will adopt the new circuit-breaker pattern.
- **NetworkPolicy adoption fleet-wide**: This plan introduces the first NetworkPolicy in the cluster as a precedent. A separate effort to backfill policies for existing apps is deferred.
- **Image digest pinning fleet-wide**: This plan starts with short-SHA tags matching prevailing convention; a follow-up effort to migrate everything to digest pins is deferred (open issue per the 04-19 Bitnami learning).
- **Portfolio writeup**: A retrospective for `quasarlab-portfolio` once the bridge is in production for 30 days.

## Context & Research

### Relevant Code and Patterns (k8s-argocd)

- `infrastructure/monitoring/discord-alert-proxy/` is the canonical FastAPI-on-K8s template. Mirror its `Dockerfile`, `deployment.yaml`, `service.yaml`, `pdb.yaml`, `externalsecret.yaml`, and `kustomization.yaml` shape verbatim. Themed embed colors and severity emojis from `proxy.py` should be copied (not shared via library) into claude-bridge to keep visual parity.
- `infrastructure/external-secrets/cluster-secret-store.yaml` defines the `onepassword-infrastructure` ClusterSecretStore. ExternalSecrets reference items by `<1P-item-id>/<field-name>`, NOT `op://`. Always set `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true`.
- `apps/dashboard/homepage/` shows the Helm-rendered `bjw-s/app-template` pattern. Not used here; we follow the hand-written manifest pattern of `discord-alert-proxy`.
- `apps/science/{servicemonitor,prometheus-rules,resource-quota}.yaml` shows the kube-prometheus-stack wiring: `release: kube-prometheus-stack` label on every ServiceMonitor/PrometheusRule, `argocd.argoproj.io/sync-wave: '2'` on the ServiceMonitor.
- `bootstrap/applicationset.yaml` generates one Application per `environments/<env>/`, NOT per app directory. New app groups MUST be registered by adding them to `environments/dev/kustomization.yaml` (correction from v1 draft of this plan).
- `environments/dev/kustomization.yaml` references each app group. Unit 10 adds `apps/automation/` here.
- `.github/workflows/build-discord-alert-proxy.yml` shows the CI shape: Buildx + GHCR + Trivy HIGH/CRITICAL gate + SARIF upload + weekly cron rebuild + Dependabot. Copy verbatim into claude-bridge.

### Institutional Learnings

- `feedback_no_rollout_restart_arr.md` and the 03-15 *arr corruption: SQLite on NFS PVC corrupts under overlapping pods. **Resolved by choosing Postgres on the existing TimescaleDB VM**, eliminating the SQLite-on-K8s risk entirely.
- `k8s-argocd/2026-04-13_alerting_blackout.md`: never host the alerter on the thing it monitors as a single replica. The bridge is paging surface; deploy with 2 replicas, podAntiAffinity, PDB `minAvailable: 1`, `priorityClassName: system-cluster-critical`. Postgres backing makes multi-replica safe.
- `k8s-argocd/2026-04-19_bitnami_images_removed.md`: pin images, no `:latest`. Use the Trivy-gated CI from discord-alert-proxy. Open issue: digest pinning is aspirational across the lab; this plan uses short-SHA tags for now.
- `feedback_op_rate_limit_care.md` and `project_2026-04-18_eso_ratelimit_recurrence.md`: 1P rate limit is per-account. New ExternalSecrets count toward the same bucket. ESO refresh is 24h; after secret rotation, kick a manual sync.
- `feedback_eso_sync_after_1p_change.md`: after rotating any 1P item the bridge depends on, force ESO sync.
- `project_2026-04-22_secrets_iac_rollout.md`: Phase 0 ESO circuit-breaker (k8s-argocd#147/#148) is in flight. Coordinate ExternalSecret introduction with that work.
- `ansible-quasarlab/2026-04-19_dynamic_inventory_op_cache_bypass.md`: do not introduce a new pattern that calls `op read` per fork. The bridge uses ESO, not `op` CLI directly. Confirmed safe.
- Decommissioning checklist in `~/.claude/CLAUDE.md`: bridge teardown must remove Prometheus targets, AlertManager routes, Grafana dashboards, NPM upstreams. Documented in the runbook.

### External References

- Discord intents and slash commands: [Discord Gateway docs](https://docs.discord.com/developers/events/gateway), [Discord Interactions](https://docs.discord.com/developers/interactions/overview). Slash commands via Gateway require no inbound HTTP; interaction object provides cryptographically authenticated `interaction.user.id`.
- discord.py 2.6.x: [docs](https://discordpy.readthedocs.io/en/stable/intents.html). Single-process pattern with FastAPI lifespan + `asyncio.create_task`: [haykkh gist](https://gist.github.com/haykkh/49ed16a9c3bbe23491139ee6225d6d09).
- FastAPI 0.135.x with Pydantic v2: [release notes](https://fastapi.tiangolo.com/release-notes/). `Annotated` style for parameters, no Pydantic v1 imports.
- Postgres on TimescaleDB VM: existing instance, schema-per-app convention.
- Approval token design: HMAC binding pattern from [OneUptime: HMAC API signing](https://oneuptime.com/blog/post/2026-01-25-secure-apis-hmac-request-signing-go/view), single-shot nonce per [OWASP CSRF cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
- Fail-closed defaults: [HashiCorp Vault Policies](https://developer.hashicorp.com/vault/docs/concepts/policies), [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html) explicitly codifies fail-closed-on-timeout for dangerous AI actions.
- Redaction: [Microsoft Presidio](https://github.com/microsoft/presidio) considered and rejected for the inline path (NLP overhead). Regex pack inline + Presidio reserved for human-readable text fields if needed.
- HITL agent patterns: [LangGraph 2.0 interrupt](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt) (Feb 2026), [AWS Bedrock Agents return-of-control](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html), [OpenAI Codex agent approvals](https://developers.openai.com/codex/agent-approvals-security). Inform the design but not directly reused.
- Claude Code hooks: [official hooks reference](https://code.claude.com/docs/en/hooks). PreToolUse JSON output schema: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow|deny|ask|defer", "permissionDecisionReason": "..."}}`. Exit code 2 = blocking error.

## Key Technical Decisions

- **State store: Postgres on TimescaleDB VM (192.168.1.123)**, not SQLite on PVC. Eliminates NFS+SQLite corruption risk (per 03-15 incident), simplifies backup. Bridge owns its own database `claude_bridge` (database-per-app, not schema-per-app) with Alembic migrations. Created on the existing TimescaleDB VM via `ansible-quasarlab` PR #125 on 2026-04-26. Schema-per-app convention is asserted by lab CLAUDE.md but must be verified in Unit 2 before Unit 3 begins.
- **DB role least privilege**: `claude_bridge_app` role has `INSERT` plus `SELECT` on `audit_log`, no `UPDATE`/`DELETE`/`TRUNCATE`. `BEFORE UPDATE` trigger is defense-in-depth; role-level deny is the primary control. Migrations run under a separate `claude_bridge_migrate` role only during deploy.
- **Single replica deployment with PDB**, NOT two replicas. Two Gateway-connected replicas would receive the same `interaction_create` event for slash commands; one's deferred response would time out and Discord would mark the interaction failed. Single replica with `priorityClassName: system-cluster-critical`, PDB `minAvailable: 1`, fast restart on failure (15s `terminationGracePeriodSeconds`). Multi-replica with leader-elected Gateway client is deferred until measured demand justifies the complexity.
- **Approval input UX: Discord slash commands** (`/approve`, `/deny`, `/status`, `/undo`). Avoids the privileged `MESSAGE_CONTENT` intent. Provides cryptographically authenticated `interaction.user.id`. Mobile-friendly autocomplete.
- **API key scope separation**: distinct keys for local hooks (allowed Tiers 1, 2, 3) and remote scheduled agents (allowed Tiers 1, 2 only). Tier 3 from a remote agent is rejected with 403. Audit log captures `actor_source` so reviewers always know whether a request originated from the operator's local session or from a scheduled remote agent.
- **Allowlist anomaly detection**: PrometheusRule fires when any allowlisted user ID issues more than 5 `/approve` calls in a 10-minute window (signal of Discord account takeover). The bridge does not auto-disable, but the alert pages immediately.
- **Token format: 22-char base64url from `secrets.token_urlsafe(16)`** = 128-bit entropy. Bound via HMAC over `(action_id, command_hash, requester_id, expiry)` and verified server-side. Single-shot, 15-min TTL. HMAC secret loaded into a `bytearray` (mutable) and overwritten on graceful shutdown to limit residency in heap (best-effort; Python string interning makes full zeroing impossible).
- **Discord client: discord.py 2.6.x with Gateway intents** (no `MESSAGE_CONTENT`). Single process with FastAPI lifespan running both the HTTP API and the Gateway WebSocket.
- **Hook gating: PreToolUse `permissionDecision: "ask"` + polling**, not blocking. The hook posts the action to the bridge, then polls `GET /approval/{action_id}` with idempotent ETag-based requests until the decision is in or TTL expires. Fails closed on bridge unreachable, redaction blocked, or TTL expiry.
- **Identity binding: server-side allowlist of Discord user IDs**, not Discord roles or usernames. The bridge stores allowlisted user IDs in 1Password (`claude-bridge/allowlist`) rotated via ESO.
- **Redaction: regex pack inline, fail-closed on match, ops-channel surfacing.** Suppressed messages emit a `redaction_blocked` event to a dedicated `bot-activity` thread so suppression is observable, not silent.
- **Outbox pattern for Tier 2 auto-proceed**: action stays in `pending_notify` until Discord post succeeds; only then does the auto-proceed countdown start. Network partition between bridge and Discord cannot trigger silent auto-proceed.
- **Audit log: append-only Postgres table** (no hash chain). `INSERT`-only via DB role grants plus `BEFORE UPDATE` / `BEFORE DELETE` triggers as defense-in-depth. Nightly `pg_dump --table=audit_log` exported to TrueNAS dataset, GPG-signed with a key held off-cluster on the operator workstation (operator-side signing means a compromised bridge cannot forge a signed export). 90-day minimum, 1-year target. Hash-chain rigor was considered and rejected as theatre against this threat model: a compromised bridge could insert forged rows regardless, and concurrent multi-replica writes would create chain forks. The simpler design provides the same forensic property (detect tampering after the fact via signed offsite copies) without runtime cost or false rigor.
- **Hook is defense-in-depth, not the primary gate.** Documented explicitly: agents calling wrappers, manual user invocations, and out-of-Claude-Code paths bypass the hook. Future PATH-shim work (deferred) covers the remaining surface.
- **Bootstrap exemption: `argocd` namespace and the bridge's own deployment** are exempt from Tier 3 gating. Enforcement: GitHub branch protection on `k8s-argocd` requires CODEOWNERS approval (one reviewer beyond the author) for PRs touching `apps/automation/claude-bridge/`, `bootstrap/`, `environments/dev/kustomization.yaml`. Without this, "2-person review" is documented hope, not enforced control. Adding CODEOWNERS is a Unit 10 deliverable.
- **Break-glass: rotating daily passphrase from 1P** (`claude-bridge/break-glass`) plus signed audit entry. Used only for cluster-down or bridge-down emergencies. Available in BOTH the Phase 0 hard-block hook AND the Phase 1 polling hook (so the operator can recover the cluster even when the bridge is in the cluster being recovered, the documented PVE-outage class of incident). Rotation: a small cron job in the bridge updates the 1P item daily at 04:00. Delivery to operator: 1Password mobile app at incident time, copied as a per-invocation env var, never in shell rc files. Phase 0 ships its own initial static passphrase before the bridge cron exists (manually rotated until Phase 1 lands).
- **Phase 0 ships before the bridge.** A pure-local `PreToolUse` hook that hard-refuses Tier 3 commands lands first, with no service dependency. Even if Phase 1 takes a week, dangerous commands are protected from day one. Phase 0 estimated at 1 day (not "afternoon"); shell-quoting defense and override mechanism are non-trivial.
- **Cluster-down recovery posture:** bridge unreachable means Tier 3 fails closed. The operator uses the break-glass passphrase to bypass the hook for the duration of recovery; every override invocation is loud-audited locally to `~/.claude/audit-phase0.log` and reconciled into the bridge audit log on next bridge startup. This explicitly inverts the default fail-closed for the documented incident class without weakening it for routine agent operation.

## Open Questions

### Resolved During Planning

- **State store choice**: Postgres on existing TimescaleDB VM at 192.168.1.123. (User confirmed; IP verified via `infrastructure/monitoring/kube-prometheus-stack/values.yaml`.)
- **Manifest location**: `k8s-argocd/apps/automation/claude-bridge/`. (User confirmed.)
- **ESO Phase 0 sequencing**: proceed in parallel; circuit-breaker retrofit deferred to follow-up tracked under k8s-argocd#147 when #148 merges.
- **Approval UX**: Discord slash commands `/approve`, `/deny`, `/status`, `/undo`. (User confirmed.)
- **Repo location**: new repo at `/home/ladino/code/claude-bridge/` for service code; manifests in k8s-argocd. (User confirmed.)
- **Privileged intents**: not required because slash commands carry the user identity in the interaction object.
- **Hook return semantics**: use `permissionDecision: "ask"` with deferred polling, not synchronous blocking. Bridge is authoritative on TTL.
- **Replica count**: single replica with PDB (multi-replica Gateway double-fires interactions). Resolved during deepening review.
- **Audit log durability**: append-only table with role-level deny + DB trigger; nightly GPG-signed pg_dump off-cluster. No hash chain. Resolved during deepening review.
- **Discord rate-limit handling**: rely on discord.py 429 backoff; no custom token-bucket queue. Resolved during deepening review.
- **API key scope**: separate keys for local hooks (all tiers) vs remote agents (Tiers 1, 2 only). Resolved during deepening review.
- **DB roles**: `claude_bridge_app` (INSERT/SELECT, no UPDATE/DELETE/TRUNCATE on audit_log) and `claude_bridge_migrate` (deploy-time only). Resolved during deepening review.
- **CNI**: Calico (verified in repo runbooks); standard NetworkPolicy enforced; FQDN egress via Calico CRDs deferred.
- **Cluster-down recovery posture**: emergency override available in BOTH Phase 0 and Phase 1 hooks; explicit inversion of fail-closed for the documented PVE-outage class of incident.
- **ArgoCD app sync as Tier 2**: NOT supported initially because ApplicationSet enables `automated.selfHeal: true`; ArgoCD changes go through Tier 3 with Git revert as the inverse.

### Deferred to Implementation

- Exact Postgres connection pool size and timeout values (depends on observed traffic).
- Exact Trivy CVE thresholds for failing the build (start with HIGH/CRITICAL like discord-alert-proxy, tune from CI feedback).
- Whether to use Alembic vs raw SQL for migrations (Alembic is the FastAPI default; confirm by trying the first migration).
- Whether discord.py's slash-command `defer()` window needs explicit response within 3s under p99 load (test once Postgres is wired up; if so, switch to deferred response pattern).
- Final list of UNDO contracts in the registry (start with `kubectl scale`, `argocd app sync`, `git revert`; expand as Tier 2 surface grows).
- Exact regex pack for redaction (start with the canonical AWS / GCP / Azure / GitHub / 1P / JWT / private key set; tune from real false positives).
- Whether to expose Prometheus metrics on `/metrics` via `prometheus-fastapi-instrumentator` or hand-rolled. Default: instrumentator.
- Hook portability: whether the hook script lives in `~/.claude/hooks/` (per-user) or in a shared dotfiles repo (per-machine). Default: `~/.claude/hooks/` with the script committed to a `claude-config` repo for reuse.

## Output Structure

```
claude-bridge/                                    (NEW REPO)
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── Dockerfile
├── .dockerignore
├── .github/
│   ├── workflows/
│   │   └── build.yml                            (mirrors discord-alert-proxy CI)
│   └── dependabot.yml
├── src/claude_bridge/
│   ├── __init__.py
│   ├── main.py                                  (FastAPI + lifespan + Discord client)
│   ├── config.py                                (settings via Pydantic)
│   ├── discord_client.py                        (Gateway client, slash commands)
│   ├── api/
│   │   ├── notify.py                            (POST /notify)
│   │   ├── approval.py                          (GET /approval/{action_id})
│   │   ├── digest.py                            (GET /digest/today)
│   │   └── auth.py                              (X-API-Key middleware)
│   ├── domain/
│   │   ├── state_machine.py                     (action states + transitions)
│   │   ├── tokens.py                            (HMAC token issuance/verification)
│   │   ├── redaction.py                         (regex pack, fail-closed)
│   │   ├── audit.py                             (append-only, hash-chained)
│   │   ├── outbox.py                            (Tier 2 outbox + auto-proceed)
│   │   └── allowlist.py                         (Discord user ID allowlist)
│   │   # NOTE: undo cases (kubectl scale, git revert, file revert) are inlined in api/notify.py;
│   │   # no separate undo_registry module until there are >5 cases or external contributors.
│   ├── persistence/
│   │   ├── db.py                                (asyncpg pool)
│   │   ├── models.py                            (SQLAlchemy 2.0 typed)
│   │   └── migrations/                          (alembic)
│   └── observability/
│       └── metrics.py                           (prometheus-fastapi-instrumentator)
│       # tracing.py deferred; no tracing requirement is stated and the cluster has no tracing backend wired up
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/synthetic_secrets.txt           (golden file for redaction)
├── docs/
│   ├── architecture.md
│   ├── runbooks/
│   │   ├── break-glass.md
│   │   ├── bridge-down.md
│   │   └── decommission.md
│   └── plans/
│       └── 2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md  (this file, copied at scaffold time)

k8s-argocd/                                       (EXISTING REPO)
├── apps/automation/                              (NEW GROUP)
│   ├── kustomization.yaml
│   ├── automation-namespace.yaml
│   └── claude-bridge/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── pdb.yaml
│       ├── externalsecret.yaml
│       ├── networkpolicy.yaml                   (NEW PATTERN: egress to discord.com + postgres only)
│       ├── servicemonitor.yaml
│       ├── prometheus-rules.yaml
│       └── kustomization.yaml
├── environments/dev/
│   └── kustomization.yaml                        (MODIFY: add apps/automation)
└── docs/plans/
    └── 2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md  (this file)

~/.claude/                                        (LOCAL)
├── settings.json                                 (MODIFY: add PreToolUse hook)
└── hooks/
    ├── tier3_gate.sh                            (NEW: hard-block in Phase 0, polling in Phase 1)
    ├── tier2_propose.sh                         (NEW: Phase 2)
    └── audit_logger.sh                          (NEW: PostToolUse, Phase 2)
```

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Approval state machine

```
                   redact_block
                  ┌───────────────► redaction_blocked (terminal)
                  │
   create        │            post_ok                 reply_received                       commit_ok
  ───────► pending_notify ─────────────► awaiting_response ─────────────► committing ───────────────► approved
                  │                              │                              │
                  │ post_fail (retryable)        │ ttl_expired                 │ commit_fail (retryable)
                  ▼                              ▼                              ▼
            pending_notify ◄─backoff─┐    expired (terminal)            committing ◄─backoff─┐
                  │                  │                                          │
                  │ post_fail (max retries)                                     │ commit_fail (max retries)
                  ▼                                                             ▼
            notify_failed (terminal)                                       commit_failed (terminal)

  Tier 1 / Tier 2 only:                    awaiting_response ──ttl_expired──► auto_approved (Tier 2 only) ───► approved
  Tier 1: skips approval entirely; create ──────► auto_approved ─────► approved
```

### Approval token issuance and verification

```
issue(action):
  expiry  = now() + 15min
  payload = (action.id, hash(action.command), action.requester_id, expiry)
  raw     = secrets.token_urlsafe(16)
  sig     = HMAC_SHA256(server_secret, raw || canonical(payload))
  token   = base64url(raw || sig)
  store nonce(token_id=hash(raw), payload, status=pending)
  return token

verify(token, claimed_action_id):
  raw, sig = split(b64decode(token))
  expected_sig = HMAC_SHA256(server_secret, raw || canonical(payload_from_db(raw)))
  if not constant_time_compare(sig, expected_sig): return invalid
  if nonce(raw).status != pending:                  return replayed
  if nonce(raw).action_id != claimed_action_id:     return mismatched
  if now() > nonce(raw).expiry:                     return expired
  if requester_id != allowlisted_user_id:           return unauthorized
  atomically: nonce.status = consumed
  return ok
```

### Hook -> Bridge flow (Tier 3, slash-command UX)

```
┌─────────────┐                           ┌──────────────┐                ┌─────────┐
│ Claude Code │                           │ claude-bridge │                │ Discord │
│  PreToolUse │                           │   FastAPI     │                │ Gateway │
└──────┬──────┘                           └───────┬───────┘                └────┬────┘
       │                                          │                              │
       │  POST /notify(tier=3, command, ...)      │                              │
       ├─────────────────────────────────────────►│                              │
       │                                          │  redact + persist row        │
       │                                          │  (state=pending_notify)      │
       │                                          │  channel.send(embed +       │
       │                                          │  "/approve <token>")         │
       │                                          ├─────────────────────────────►│
       │                                          │  state=awaiting_response     │
       │  202 Accepted {action_id, ttl}           │                              │
       │◄─────────────────────────────────────────┤                              │
       │                                          │                              │
       │  decision: "ask" (with reason)           │                              │
       │ (returns to Claude harness, hook polls)  │                              │
       │                                          │                              │
       │  GET /approval/{action_id}               │                              │
       ├─────────────────────────────────────────►│                              │
       │  200 {state=awaiting_response, ...}      │                              │
       │◄─────────────────────────────────────────┤                              │
       │                                          │                              │
       │                                          │  user issues /approve <token>│
       │                                          │  via slash command           │
       │                                          │◄─────────────────────────────┤
       │                                          │  verify(token, action_id):   │
       │                                          │  - constant-time HMAC        │
       │                                          │  - allowlist user_id         │
       │                                          │  - single-shot nonce consume │
       │                                          │  state=committing→approved   │
       │                                          │  bot replies "approved"      │
       │                                          ├─────────────────────────────►│
       │                                          │                              │
       │  GET /approval/{action_id}               │                              │
       ├─────────────────────────────────────────►│                              │
       │  200 {state=approved, decided_by=...}    │                              │
       │◄─────────────────────────────────────────┤                              │
       │                                          │                              │
       │  PreToolUse returns "allow"              │                              │
       │  Bash command runs                       │                              │
       │  PostToolUse posts result to /notify     │                              │
       │  (tier=1 status update)                  │                              │
       │                                          │                              │
       └──────────────────────────────────────────┘                              │
```

## Implementation Units

- [ ] **Unit 1: Phase 0 hard-block hook (no bridge dependency)**

**Goal:** Ship a `PreToolUse` hook within ~1 day that refuses Tier 3 commands without any bridge running. Provides a hard floor while the bridge is being built. Note: this hook (`tier3_gate.sh`) is NOT replaced by Unit 7. Unit 7 ADDS a sibling `tier3_gate_polling.sh`; the user enables one or the other via a feature flag in `~/.claude/hooks/.env` and can keep the hard-block as a permanent fallback when the bridge is offline.

**Requirements:** R3, R9.

**Dependencies:** None.

**Files:**
- Create: `~/.claude/hooks/tier3_gate.sh`
- Modify: `~/.claude/settings.json` (add `PreToolUse` hook entry on `Bash` matcher)
- Create: `claude-bridge/docs/runbooks/break-glass.md` (initial Phase 0 emergency override doc)

**Approach:**
- Shell script reads `PreToolUse` JSON from stdin, extracts `tool_name` and `tool_input.command`.
- Match against a curated deny list: `terraform apply`, `terraform destroy`, `kubectl delete`, `kubectl scale --replicas=0` (when not from undo), `git push --force`, `git push -f`, `op item delete`, `op vault delete`, `ansible-playbook .* --tags .*destroy`, `helm uninstall`, `argocd app delete`, `rm -rf` against `/home`, `/etc`, `/var`, `/mnt`.
- On match: emit `permissionDecision: "deny"` JSON with reason citing Phase 0 and pointing at the break-glass doc.
- Emergency override: if env var `CLAUDE_BRIDGE_EMERGENCY_OVERRIDE` matches today's rotating passphrase from `claude-bridge/break-glass` 1P item, allow with a loud audit log entry written to `~/.claude/audit-phase0.log`.

**Patterns to follow:**
- Hook syntax from [Claude Code hooks docs](https://code.claude.com/docs/en/hooks).
- Deny-list shape from `block-rm.sh` example in the same docs.

**Test scenarios:**
- Happy path: `terraform apply` is denied with a clear message citing Phase 0.
- Happy path: `kubectl delete pod foo` is denied.
- Edge case: `terraform plan` is allowed (planning is safe).
- Edge case: `kubectl get pods` is allowed.
- Edge case: `terraform apply --target=module.docs` is denied (matcher must catch arg variants).
- Edge case: a heredoc command containing `terraform apply` inside a `bash -c "..."` is denied (matcher must catch `bash -c` wrappers).
- Error path: malformed JSON on stdin produces `permissionDecision: "deny"` with reason "hook parse error" and exits 2.
- Integration: emergency override env var matches today's passphrase, command runs, audit log entry written with timestamp, command, and reason.
- Integration: emergency override with yesterday's passphrase is denied (rotation works).

**Verification:**
- Running `terraform apply` against a real plan in a test directory is blocked.
- Running `kubectl delete --dry-run=client deployment foo` is blocked (the matcher must not be defeated by `--dry-run`).
- Audit log file created on first emergency override use.

---

- [ ] **Unit 2: claude-bridge repo scaffolding and CI**

**Goal:** Create the new `claude-bridge` repo with the same operational shape as `discord-alert-proxy`. Empty service, but build pipeline, image publishing, Trivy scanning, and Dependabot all working.

**Requirements:** R7, R10.

**Dependencies:** Unit 1 (so Phase 0 protection is in place before the bridge exists).

**Files:**
- Create: `pyproject.toml` (Python 3.12, `fastapi==0.135.*`, `uvicorn[standard]`, `discord.py>=2.6,<3`, `asyncpg`, `sqlalchemy[asyncio]>=2`, `pydantic>=2.7`, `prometheus-fastapi-instrumentator`, `alembic`)
- Create: `Dockerfile` (multi-stage, `python:3.12-slim`, non-root UID 10001, readOnlyRootFilesystem-friendly)
- Create: `.dockerignore`
- Create: `.github/workflows/build.yml` (mirrors `k8s-argocd/.github/workflows/build-discord-alert-proxy.yml`)
- Create: `.github/dependabot.yml` (pip + docker + actions)
- Create: `README.md`, `CLAUDE.md`
- Create: `src/claude_bridge/__init__.py`, `src/claude_bridge/main.py` (minimal FastAPI hello)
- Create: `tests/unit/test_health.py`

**Approach:**
- Trivy gate at HIGH/CRITICAL.
- GHCR image at `ghcr.io/<org>/claude-bridge`.
- Image pinned by 12-char short SHA (matches existing convention; digest pinning deferred fleet-wide).
- Weekly cron rebuild for base-image CVE refresh.
- `CLAUDE.md` carries the global security-first directive plus repo-specific rules: never log secrets, redaction is fail-closed, hook is defense-in-depth.

**Patterns to follow:**
- `infrastructure/monitoring/discord-alert-proxy/Dockerfile`
- `.github/workflows/build-discord-alert-proxy.yml`

**Test scenarios:**
- Happy path: `docker build .` produces an image that runs `uvicorn` and serves `GET /healthz` returning 200.
- Happy path: GitHub Actions build runs Trivy and uploads SARIF.
- Edge case: `pip install` honors the lockfile (deterministic build, no upstream version drift).
- Error path: an intentionally added vulnerable dependency fails the Trivy gate.

**Verification:**
- First successful image push to GHCR with Trivy report attached.
- Dependabot opens its first PR within 24h.

---

- [ ] **Unit 3: Postgres schema and persistence layer**

**Goal:** Design and migrate the `claude_bridge` schema in the existing TimescaleDB Postgres. Connection pool, models, basic CRUD.

**Requirements:** R5, R7.

**Dependencies:** Unit 2.

**Files:**
- Create: `src/claude_bridge/persistence/db.py` (asyncpg pool, Pydantic settings)
- Create: `src/claude_bridge/persistence/models.py` (SQLAlchemy 2.0 typed models)
- Create: `src/claude_bridge/persistence/migrations/env.py`, `versions/0001_initial.py`
- Create: `tests/integration/test_persistence.py`

**Approach:**
- Database: `claude_bridge` (separate Postgres database on 192.168.1.123, owned by `claude_bridge_migrate`). Provisioned via `ansible-quasarlab` PR #125 on 2026-04-26.
- Tables:
  - `actions(action_id pk uuid, tier int, command_hash text, command_redacted text, requester text, actor_source text check in ('local','remote'), created_at, state text, decided_by text null, decided_at null, channel_message_id text null, ttl_expiry timestamptz, prev_state text null)`
  - `nonces(token_hash bytea pk, action_id fk, payload jsonb, status text check in ('pending','consumed','expired'), created_at, expiry timestamptz)`
  - `audit_log(seq bigserial pk, ts timestamptz, action_id fk, actor text, actor_source text, decision text, redacted_payload jsonb)` (append-only; no hash chain; defense-in-depth via DB role plus triggers)
  - `outbox(id pk, action_id fk, posted_at null, retry_count int, last_error text null, status text check in ('pending','posted','failed'), notify_idempotency_key text)` 
  - `allowlist(discord_user_id text pk, label text, added_at, source text check in ('eso','manual'))`
  - `undo_decisions(undo_id pk, original_action_id fk, undo_command jsonb, expires_at)`
- All enum-shaped columns use `text` plus `CHECK` constraint, NOT native Postgres enums (avoids Alembic migration friction with `ALTER TYPE ADD VALUE`).
- `audit_log` is append-only via TWO controls: (a) `claude_bridge_app` DB role lacks `UPDATE`/`DELETE`/`TRUNCATE` privilege on this table, (b) `BEFORE UPDATE` and `BEFORE DELETE` triggers raise. No hash chain.
- `nonces.token_hash` is the SHA-256 of the raw token, not the token itself.
- Connection pool: asyncpg, 10 connections, 30s timeout.
- Database-per-app convention (resolved 2026-04-26): a dedicated `claude_bridge` database was created on 192.168.1.123 with `claude_bridge_migrate` as the owner. The bridge connects via `claude_bridge_app` for runtime and `claude_bridge_migrate` for Alembic migrations only. Privileges on `audit_log` (INSERT + SELECT only for app role) are applied by the migration that creates the table, not by the Ansible role.

**Patterns to follow:**
- Existing TimescaleDB connection patterns (verify schema-per-app convention with the running DB).

**Test scenarios:**
- Happy path: insert an action and read it back with both `actor_source='local'` and `actor_source='remote'` variants.
- Edge case: attempting `UPDATE audit_log` from the `claude_bridge_app` role fails on permission denied (primary control).
- Edge case: attempting `UPDATE audit_log` while bypassing role check (e.g., as superuser in test) raises from the `BEFORE UPDATE` trigger (defense-in-depth).
- Edge case: nonce status check rejects invalid values via CHECK constraint.
- Edge case: `actor_source` CHECK constraint rejects values outside `('local','remote')`.
- Error path: connection pool exhaustion returns 503 with retry hint.
- Integration: Alembic upgrade + downgrade round-trips cleanly.

**Verification:**
- `alembic upgrade head` against a test Postgres produces the expected schema.
- Privilege test: `claude_bridge_app` cannot UPDATE/DELETE/TRUNCATE `audit_log`; `claude_bridge_migrate` can run schema migrations but is only used during deploys.

---

- [ ] **Unit 4: Outbound redaction layer**

**Goal:** Regex-based redaction of secrets in outbound payloads. Fail-closed on detection. Surface blocks to a dedicated `bot-activity` thread.

**Requirements:** R4, R9.

**Dependencies:** Unit 2.

**Files:**
- Create: `src/claude_bridge/domain/redaction.py`
- Create: `tests/unit/test_redaction.py`
- Create: `tests/fixtures/synthetic_secrets.txt` (golden file, real-shape fake secrets)

**Approach:**
- Regex pack covers: AWS access keys (`AKIA[0-9A-Z]{16}`), AWS secret keys (40-char base64), GitHub PATs (`ghp_`, `gho_`, `ghs_`, `ghr_`, `ghu_`), GCP service account JSONs (`"type": "service_account"`), Azure connection strings, Slack tokens (`xoxb-`, `xoxp-`), 1Password Connect tokens (`ops_`), Discord bot tokens (3-segment base64 with specific lengths), JWTs (3-segment base64 with `eyJ` prefix), private keys (`-----BEGIN .* PRIVATE KEY-----`), generic high-entropy 32+ char hex/base64.
- IP redaction: replace `192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12` with `[REDACTED:internal-ip]` (configurable).
- Normalize input: NFKC + strip control chars + lowercase for matching against base patterns (preserve original for output replacement).
- API: `redact(text: str) -> RedactionResult(redacted_text, matches: list[(category, original_len)], blocked: bool)`. Blocked when any high-severity category matched (everything except internal-ip).
- On block: do NOT send to original channel. Post a synthetic message to `bot-activity` thread `redaction-blocked` with action_id, redaction categories matched (no payload, no secrets), and reason. Operator can investigate via the bridge `/status` endpoint or `kubectl logs`.

**Patterns to follow:**
- `discord-alert-proxy` themed embed format (color = warning orange for blocks).

**Test scenarios:**
- Happy path: text with no secrets passes unchanged, `blocked=False`.
- Edge case (each pattern category): a synthetic AWS key in the payload matches and triggers `blocked=True`.
- Edge case: lowercase vs mixed case AWS key both match.
- Edge case: a real GitHub PAT in a multi-line code block matches.
- Edge case: a base64-wrapped secret (secret split into `<header>.<base64>.<sig>`) is detected by JWT regex.
- Edge case: zero-width characters in a token still trigger detection (after NFKC + control-char strip).
- Edge case: secret split across two messages by sender is NOT detected (documented limitation, mitigated by per-call redaction; no cross-call state).
- Error path: malformed input (non-string) raises a typed error.
- Integration: a Tier 1 status post with a real-shape synthetic AWS key is blocked, original message NOT posted to channel, redaction-blocked notice IS posted.

**Verification:**
- Golden-file test: `tests/fixtures/synthetic_secrets.txt` contains one line per category; redactor matches each and produces zero false negatives. Track false positive rate against a curated `tests/fixtures/safe_payloads.txt`.

---

- [ ] **Unit 5: Discord client and slash commands (Gateway)**

**Goal:** Bring the discord.py 2.6 Gateway client up under the FastAPI lifespan. Register slash commands `/approve`, `/deny`, `/status`, `/undo`. Wire to bridge state.

**Requirements:** R1, R2.

**Dependencies:** Units 2, 3, 4.

**Files:**
- Create: `src/claude_bridge/discord_client.py`
- Modify: `src/claude_bridge/main.py` (lifespan starts client)
- Create: `tests/integration/test_discord_commands.py`

**Approach:**
- Single process, FastAPI lifespan creates `asyncio.create_task(client.start(token))`. On shutdown: `await client.close()`, then close DB pool, then exit.
- Intents: `Intents.default()` plus `guild_reactions=True`. NO `message_content`.
- Slash commands:
  - `/approve <token:str>`: defer ephemeral, verify token, post public confirmation if approved.
  - `/deny <token:str>`: defer ephemeral, verify token, post public denial.
  - `/status <action_id:str>`: defer ephemeral, look up action state.
  - `/undo <undo_id:str>`: defer ephemeral, look up undo entry, issue new Tier 3 approval request for the undo command.
- Discord 3-second window: every command uses `interaction.response.defer(ephemeral=True)` immediately, then `interaction.followup.send(...)`. Bridge-side processing has up to 15 minutes after defer.
- Outbound: `channel.send()` for normal embeds. `allowed_mentions=AllowedMentions.none()` everywhere to prevent `@everyone` injection via reflected user input. Tier 3 messages mention the user via `<@user_id>` with `allowed_mentions=AllowedMentions(users=[user])`.
- Rate-limit handling: rely on discord.py's built-in 429 backoff. No custom token-bucket queue (cut from v1 draft as YAGNI for single-user homelab traffic). If observation shows bursts >5 messages / 5 seconds in the same channel, revisit.
- Tier 3 embeds always include a `Source: local` or `Source: remote` badge so the operator knows whether the request originated from their interactive Claude session or from a scheduled remote agent.

**Patterns to follow:**
- discord.py lifespan pattern from the haykkh gist.
- Theme colors copied from `discord-alert-proxy/proxy.py` (red=critical, orange=warning, blue=info, green=resolved). Tier 3 uses critical embeds, Tier 2 warning, Tier 1 info, resolved on auto-proceed/approval.

**Test scenarios:**
- Happy path: `/approve <valid-token>` for an action transitions state from `awaiting_response` to `approved`, posts public confirmation.
- Happy path: `/deny <valid-token>` transitions to `denied`.
- Edge case: `/approve <expired-token>` returns "expired" ephemerally, action state unchanged.
- Edge case: `/approve <consumed-token>` returns "already used".
- Edge case: `/approve <invalid-hmac>` returns "invalid token", audit log entry `unauthorized_attempt`.
- Edge case: `/approve` from a non-allowlisted Discord user ID returns "not authorized", audit log entry.
- Error path: `/approve` when bridge DB is down returns 5xx ephemerally, action state unchanged, retry safe.
- Integration: APPROVE then DENY within 200ms results in exactly one terminal state (`approved`), DENY responder told "already approved by <user> at <ts>".
- Integration: bridge sends 10 Tier 1 embeds in 1 second, all eventually delivered without dropping (token-bucket queue).
- Integration: `allowed_mentions` test: a payload containing `@everyone` does NOT mention everyone when posted.

**Verification:**
- Manual: in a test Discord server, exercise each slash command with valid and invalid inputs.
- Integration test asserts state transitions and audit log entries for each command flow.

---

- [ ] **Unit 6: Approval token lifecycle and state machine**

**Goal:** Implement HMAC-bound token issuance, single-shot nonce consumption, and the action state machine with terminal-once semantics.

**Requirements:** R2.

**Dependencies:** Units 3, 5.

**Files:**
- Create: `src/claude_bridge/domain/tokens.py`
- Create: `src/claude_bridge/domain/state_machine.py`
- Create: `src/claude_bridge/domain/allowlist.py`
- Create: `tests/unit/test_tokens.py`, `tests/unit/test_state_machine.py`

**Approach:**
- `tokens.issue(action) -> Token`: 16-byte secret, HMAC over `(action_id, command_hash, requester_id, expiry)`, base64url. Server secret stored in 1P (`claude-bridge/server-hmac-secret`), rotated monthly.
- `tokens.verify(raw, claimed_action_id, claimed_user_id) -> VerifyResult`: constant-time HMAC compare, nonce status check, action_id match check, allowlist match, expiry check, single-shot atomic update (`UPDATE nonces SET status='consumed' WHERE token_hash=$1 AND status='pending' RETURNING id` is the gate).
- State machine: explicit transition table. Allowed transitions only. Database constraints enforce: no transition out of terminal states, no two concurrent transitions (use `SELECT ... FOR UPDATE` or row-level optimistic locking).
- All decisions emit an audit_log row before the bot acks the user.
- Time skew: bridge is authoritative on `now()`, clients never decide expiry.

**Test scenarios:**
- Happy path: issue then verify returns `ok` and consumes the nonce.
- Happy path: state machine transitions `pending_notify` -> `awaiting_response` -> `approved` cleanly.
- Edge case: same token verified twice -> second returns `replayed`.
- Edge case: token bound to action A used to claim approval on action B -> `mismatched`.
- Edge case: token issued at T+0 with TTL=15min, verified at T+16min -> `expired`.
- Edge case: APPROVE and DENY race (two commands within the same DB transaction window) -> exactly one wins, other returns "already resolved".
- Edge case: clock skew of +20 minutes on the hook side does not cause early "expired" because hook never decides expiry.
- Error path: HMAC over corrupted bytes -> `invalid`, audit log entry `unauthorized_attempt`.
- Error path: server secret rotation makes existing tokens invalid (intentional; document as operational note).
- Integration: 1000 concurrent verify calls on different tokens, each succeeds exactly once.

**Verification:**
- Property-based test (Hypothesis): random tokens never verify; valid tokens verify at most once.
- Race test: two `verify()` calls on the same token from two coroutines, exactly one returns `ok`.

---

- [ ] **Unit 7: PreToolUse polling hook (sibling to Phase 0 hard-block, feature-flagged)**

**Goal:** Add a polling hook (`tier3_gate_polling.sh`) alongside the Phase 0 hard-block (`tier3_gate.sh`). The user toggles which hook runs via a feature flag in `~/.claude/hooks/.env`. Polling hook posts the action to the bridge, returns `permissionDecision: "ask"`, and waits for the bridge decision. Fails closed on bridge unreachable except when emergency override is set.

**Requirements:** R3, R9.

**Dependencies:** Units 1, 5, 6. Bridge running with API-key auth.

**Files:**
- Create: `~/.claude/hooks/tier3_gate_polling.sh` (NEW: does NOT replace tier3_gate.sh; sibling)
- Create: `~/.claude/hooks/lib/bridge_client.sh` (curl-based POST + GET helpers)
- Create: `~/.claude/hooks/.env.example` (documents `CLAUDE_BRIDGE_HOOK=hard|polling`, bridge URL, API key, emergency override semantics)
- Modify: `~/.claude/settings.json` (the `PreToolUse` hook command becomes a small dispatcher that reads the flag and invokes the right script)

**Approach:**
- POST `/notify` with `{tier:3, command, requester, actor_source: "local"}` plus `X-API-Key` header (local-scope key), get `{action_id, ttl_seconds}`.
- Output `permissionDecision: "ask"` to Claude harness with reason "Awaiting Discord approval. action_id=...".
- Poll `GET /approval/{action_id}` every 5s with `If-None-Match` ETag for cheap re-polls. Stop when state is terminal or TTL elapsed.
- Fail-closed conditions: bridge unreachable >30s, redaction_blocked state, expired state, denied state, any 5xx from bridge.
- **Emergency override available**: identical to Phase 0 hard-block hook. If `CLAUDE_BRIDGE_EMERGENCY_OVERRIDE` matches today's rotating passphrase from `claude-bridge/break-glass`, the hook returns "allow" with a loud audit-log entry to `~/.claude/audit-phase0.log` (later reconciled into the bridge audit log on next bridge connection). This handles the cluster-down recovery class of incident where the bridge itself is unreachable.
- Hook never local-caches decisions, never decides expiry locally.
- Bridge URL and API key from `~/.claude/hooks/.env` (gitignored, rotated via `claude-config` repo's secret pattern).

**Test scenarios:**
- Happy path: hook calls bridge, gets action_id, polls until approved, returns "allow" within TTL.
- Happy path: hook calls bridge, gets action_id, polls until denied, returns "deny".
- Edge case: bridge returns redaction_blocked -> hook returns "deny" with reason and pointer to ops channel.
- Error path: bridge unreachable on initial POST -> hook returns "deny" within 30s with reason "bridge unreachable".
- Error path: bridge unreachable mid-poll -> hook continues polling for 30s, then "deny".
- Edge case: TTL elapses without decision -> hook returns "deny" with reason "approval expired".
- Edge case: clock skew on hook host -> hook does not pre-emptively expire; bridge decision wins.
- Integration: real bridge running, hook on a test command, full happy and unhappy paths exercised.

**Verification:**
- Manual: run a benign Tier 3 command end-to-end with bridge online and offline.
- Integration test in CI exercises hook against a containerized bridge.

---

- [ ] **Unit 8: Tier 2 outbox, auto-proceed scheduler, and inline UNDO**

**Goal:** Wire Tier 2 actions: post to `bot-proposals`, default-proceed after timeout, record undo. Outbox pattern guarantees no auto-proceed without confirmed Discord post.

**Requirements:** R1, R6.

**Dependencies:** Units 5, 6.

**Files:**
- Create: `src/claude_bridge/domain/outbox.py`
- Modify: `src/claude_bridge/api/notify.py` (Tier 2 path; UNDO cases inlined as a small `if/elif` block; no separate registry module)
- Modify: `src/claude_bridge/discord_client.py` (auto-proceed scheduler runs as an `asyncio.create_task`)
- Create: `tests/integration/test_tier2_flow.py`

**Approach:**
- `POST /notify` with `tier=2, auto_proceed_after_seconds=N (default 1800)` writes the action row AND the outbox row in one transaction (transactional outbox). The outbox worker reads pending rows, posts to Discord, then in a SECOND transaction transitions state to `awaiting_response` and starts TTL countdown.
- Auto-proceed scheduler: every 30s scans `actions WHERE tier=2 AND state=awaiting_response AND ttl_expiry < now() + slop`. Atomically updates to `auto_approved` via `UPDATE ... WHERE state='awaiting_response' RETURNING ...`, posts confirmation embed in `bot-proposals` ("auto-proceeded after no objection"), writes audit_log row, captures the `undo_command`.
- Idempotency on Discord post retry: the outbox row carries a `notify_idempotency_key` (UUID generated at insert). The bot embeds this key as a zero-width-marker in the embed footer. On retry, the worker first queries the last 50 channel messages for the marker before re-posting. Tradeoff documented: this is best-effort, not strict exactly-once.
- **Inline UNDO cases** (no registry module). Initial supported classes, with corrected semantics:
  - `kubectl scale deployment/<ns>/<name> --replicas=N` -> capture `prev_replicas` from `kubectl get deployment` at request time. Undo: `kubectl scale ... --replicas=<prev>`. Pre-undo check: current replicas equal target; if drifted (HPA, manual scale), undo aborts and surfaces a Tier 3 review request instead.
  - `git commit + push to feature branch` -> capture pre-commit SHA. Undo: `git revert <sha> && git push`. Pre-undo check: branch is fast-forward-able from the captured SHA (no force-push since); if not, undo aborts.
  - File edits via Claude `Edit` tool -> PostToolUse hook captures pre-edit content into a tarball under `~/.claude/edit-snapshots/<action_id>.tar`. Undo: extract back over the file and post a Tier 1 confirmation.
  - `argocd app sync <app>`: **NOT initially supported as Tier 2** because the bootstrap ApplicationSet enables `automated.selfHeal: true` on every Application, so `argocd app rollback` would race with the controller re-syncing to HEAD. Until selfHeal is configurable per-app, ArgoCD changes go through Tier 3 with the undo expressed as a Git revert PR.
- Actions whose inverse cannot be expressed (e.g., `terraform apply`, deletions of unique data) MUST be classified Tier 3, NOT Tier 2. The notify endpoint enforces this: Tier 2 requires the action class to match one of the supported branches above.
- Tier 3 cannot use auto-proceed; the API rejects `auto_proceed_after_seconds` for tier=3.

**Test scenarios:**
- Happy path: Tier 2 action posted, no reply for 30 min, auto_approved state with undo_command captured.
- Happy path: Tier 2 action posted, user denies before TTL, denied state, no undo recorded.
- Edge case: Discord post fails on first attempt, retries with backoff, eventually posts, TTL countdown starts only after success.
- Edge case: Discord post fails permanently after max retries -> action moves to `notify_failed`, audit_log entry, no auto-proceed.
- Edge case: Tier 2 action with no registered undo class -> POST /notify returns 422 with "undo_class_required".
- Edge case: Tier 3 action with auto_proceed_after_seconds set -> POST /notify returns 422.
- Edge case: scheduler crashes mid-update -> on restart, atomic update prevents double auto-approve.
- Integration: 5 Tier 2 actions in 1 minute all auto-proceed correctly, audit log shows each with prev_hash chained.

**Verification:**
- Integration test seeds Tier 2 action, fast-forwards clock 30 min, asserts state and audit log.
- Restart-during-auto-proceed test: kill scheduler at the exact moment it would update, verify exactly one transition.

---

- [ ] **Unit 9: Daily digest cron and UNDO replay**

**Goal:** 06:00 daily digest of auto-proceeded Tier 2 actions in `bot-activity`. Each entry has a slash-command-callable UNDO that runs through the Tier 3 flow.

**Requirements:** R6.

**Dependencies:** Unit 8.

**Files:**
- Create: `src/claude_bridge/api/digest.py`
- Modify: `src/claude_bridge/main.py` (register 06:00 cron task)
- Modify: `src/claude_bridge/discord_client.py` (`/undo` slash command)
- Create: `tests/integration/test_digest.py`

**Approach:**
- 06:00 cron: read `audit_log` for the past 24h auto-proceeded actions. Compose a single digest embed (paginated if >25 entries) listing each with action_id, brief description, undo_id.
- `/undo <undo_id>`: looks up the recorded undo command, opens a fresh Tier 3 approval request for it, returns ephemeral confirmation with the new approval token.
- Undo TTL: 7 days from original auto-approve (configurable). After that, undo entries expire and the digest does not list them as undoable.

**Test scenarios:**
- Happy path: 3 Tier 2 actions auto-proceed during the day, digest at 06:00 lists all three with undo_ids.
- Happy path: `/undo <id>` for a recent auto-approved action opens a Tier 3 approval request for the inverse command.
- Edge case: digest for a day with zero auto-proceeded actions posts a "nothing happened" message (not silence).
- Edge case: `/undo` for an expired undo entry returns "undo no longer available".
- Edge case: `/undo` for the same undo_id called twice -> second returns "already initiated".
- Error path: scheduler missed 06:00 (pod restart) -> on next start, catch-up logic runs digest for any missed days.
- Integration: end-to-end: Tier 2 action auto-proceeds at 02:00, digest at 06:00, `/undo` at 09:00, fresh Tier 3 approval issued, user `/approve`s, undo command executes.

**Verification:**
- Manual: live test on a real day with at least one auto-proceeded action.
- Integration test seeds 24h of audit_log, asserts digest content and undo flow.

---

- [ ] **Unit 10: ArgoCD app, ESO secrets, NetworkPolicy, ServiceMonitor, PrometheusRule**

**Goal:** Deploy the bridge to the cluster via ArgoCD with the same operational rigor as `discord-alert-proxy`. New `automation` app group. First NetworkPolicy in the cluster.

**Requirements:** R7, R8, R10.

**Dependencies:** Unit 2 (image must be published).

**Files (in k8s-argocd):**
- Create: `apps/automation/kustomization.yaml`
- Create: `apps/automation/automation-namespace.yaml`
- Create: `apps/automation/claude-bridge/deployment.yaml`
- Create: `apps/automation/claude-bridge/service.yaml`
- Create: `apps/automation/claude-bridge/pdb.yaml`
- Create: `apps/automation/claude-bridge/externalsecret.yaml`
- Create: `apps/automation/claude-bridge/networkpolicy.yaml`
- Create: `apps/automation/claude-bridge/servicemonitor.yaml`
- Create: `apps/automation/claude-bridge/prometheus-rules.yaml`
- Create: `apps/automation/claude-bridge/kustomization.yaml`
- Modify: `environments/dev/kustomization.yaml` (add `apps/automation`)

**Approach:**
- Deployment: **1 replica** (NOT 2). Two Gateway-connected replicas would receive the same `interaction_create` event for slash commands; one's deferred response would time out and Discord would mark the interaction failed. Single replica with PDB `minAvailable: 1`, `priorityClassName: system-cluster-critical`, fast restart on failure (15s `terminationGracePeriodSeconds`). Multi-replica with leader-elected Gateway client deferred until measured demand justifies it.
- securityContext: non-root UID 10001, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, capabilities drop ALL, seccompProfile RuntimeDefault.
- ExternalSecret pulls (each as a SEPARATE 1P item, NOT comma-separated multi-key blobs): `claude-bridge/discord-bot-token`, `claude-bridge/server-hmac-secret`, `claude-bridge/postgres-dsn`, `claude-bridge/api-key-local-hook`, `claude-bridge/api-key-remote-agent`, `claude-bridge/allowlist`, `claude-bridge/break-glass`. `refreshInterval: 24h`. After rotation, manual ESO sync.
- API key scope separation: `claude-bridge/api-key-local-hook` authorizes Tier 1, 2, 3 from local hook callers. `claude-bridge/api-key-remote-agent` authorizes Tier 1 and 2 only; Tier 3 from a remote agent is rejected at the API layer with 403.
- DB roles: `claude_bridge_app` (used by the running pod) has `INSERT` and `SELECT` on `audit_log`, no `UPDATE`/`DELETE`/`TRUNCATE`. `claude_bridge_migrate` has full DDL and is used only at deploy time via Alembic. DB role provisioning is a one-time setup recorded in `terraform-quasarlab` (or `ansible-quasarlab`) for IaC compliance.
- NetworkPolicy: egress to Discord IP ranges (allowlisted subnets, periodically reviewed; the cluster's CNI is Calico and standard NetworkPolicy does NOT support FQDN, so the bridge accepts an IP-range-based policy with documented review cadence), Postgres VM (192.168.1.123:5432), kube-dns. Ingress only from monitoring (Prometheus scrape). No LB exposure.
- ServiceMonitor: label `release: kube-prometheus-stack`, sync-wave 2.
- PrometheusRule: alerts for bridge unreachable >2min (page immediately), redaction-blocked rate >5/min, allowlist anomaly (>5 approvals from one user in 10 minutes), Postgres connection pool exhaustion, ESO sync failure, Discord Gateway disconnected >5min, outbox stuck (rows in `pending_notify` >5 minutes).
- Image pinned by 12-char short SHA from claude-bridge GHCR build (matches prevailing lab convention; fleet-wide digest pinning is a deferred effort).
- Reloader annotation on pod template for cert/secret rotation.
- CODEOWNERS file in `k8s-argocd/.github/CODEOWNERS`: `apps/automation/claude-bridge/`, `bootstrap/`, and `environments/dev/kustomization.yaml` require approval from a designated reviewer (the operator's secondary account, or a co-maintainer if available). Branch protection rule on `main` enforces this. Without this, the bootstrap exemption is documented hope, not control.

**Patterns to follow:**
- `infrastructure/monitoring/discord-alert-proxy/` directory verbatim.
- `apps/science/{servicemonitor,prometheus-rules,resource-quota}.yaml` for Prometheus wiring.
- `infrastructure/external-secrets/cluster-secret-store.yaml` for ExternalSecret format.

**Execution note:** Coordinate the introduction of `apps/automation/claude-bridge/externalsecret.yaml` with k8s-argocd#147/#148; once #148 lands, follow up to adopt the ESO circuit-breaker pattern.

**Test scenarios:**
- Happy path: ArgoCD syncs the new app group, pods become Ready, ServiceMonitor scrapes `/metrics`.
- Happy path: PrometheusRule fires "bridge-down" alert when both replicas are unreachable.
- Edge case: NetworkPolicy blocks egress to non-Discord, non-Postgres targets (verify with `kubectl exec ... curl https://google.com` -> blocked).
- Edge case: secret rotation via 1P, manual ESO sync, Reloader bumps pods, no downtime due to PDB.
- Error path: missing ExternalSecret -> ArgoCD syncs everything else with `SkipDryRunOnMissingResource=true`, pods CrashLoop until secret arrives, alert fires.
- Integration: PrometheusRule for "audit-log hash-chain mismatch" tested by deliberately corrupting one row in a test DB and verifying the alert fires.

**Verification:**
- ArgoCD app shows synced and healthy.
- Grafana dashboard with key metrics (notify rate, approval rate, redaction blocks, Discord Gateway latency, Postgres pool usage) is provisioned.
- All PrometheusRule alerts fire correctly in a chaos test.

---

- [ ] **Unit 11: Operational runbooks and decommission checklist**

**Goal:** Document the system for operators and future-you. Cover break-glass, bridge-down, decommission.

**Requirements:** R9, decommissioning checklist from `~/.claude/CLAUDE.md`.

**Dependencies:** Units 1-10.

**Files:**
- Create: `claude-bridge/docs/runbooks/break-glass.md`
- Create: `claude-bridge/docs/runbooks/bridge-down.md`
- Create: `claude-bridge/docs/runbooks/decommission.md`
- Create: `claude-bridge/docs/architecture.md`
- Create: `claude-bridge/CLAUDE.md`
- Note: portfolio retrospective is deferred to +30 days post-production; tracked in `quasarlab-portfolio` backlog, not in this plan.

**Approach:**
- `break-glass.md`: when to use, how to get today's rotating passphrase (from 1Password mobile app, never copied to shell history), how to invoke the override (per-invocation env var only, NEVER in `.bashrc`/`.zshrc`), what audit entries are written, how to roll the passphrase if compromised. Explicitly covers the cluster-down recovery class of incident where the bridge itself is unreachable.
- `bridge-down.md`: detection (PrometheusRule), triage (Postgres? Discord Gateway? Pod? ESO?), recovery, post-incident audit-log integrity check.
- `decommission.md`: full checklist matching `~/.claude/CLAUDE.md` items: remove Prometheus targets, remove PrometheusRule, remove Grafana dashboard, remove ExternalSecrets, remove from `environments/dev/kustomization.yaml`, remove ArgoCD app, archive Postgres schema, archive audit log to TrueNAS, remove Discord channels (or keep for history), remove allowlist 1P item, remove server-hmac-secret 1P item, verify no alerts fire after.
- `architecture.md`: state machine diagram, token flow, redaction pipeline, audit log hash chain, threat model.
- `CLAUDE.md` (in claude-bridge): repo-specific conventions, security-first directive carried forward, contribution rules.
- Portfolio skeleton: README placeholder with sections for Problem, Architecture, Decisions, Operational lessons, Metrics. Filled in after 30 days in production.

**Test scenarios:**
- Manual review: walk through `bridge-down.md` against a simulated outage.
- Manual review: walk through `decommission.md` and confirm every item is in fact undone if executed.

**Verification:**
- A teammate (or future-self) following `bridge-down.md` can recover without consulting source.
- Decommission dry-run on a test deployment leaves zero residue.

---

<!-- Unit 12 (ESO circuit-breaker retrofit) was demoted to Deferred-to-Separate-Tasks during the 2026-04-26 deepening pass.
It is gated on k8s-argocd#148 merging and tracked in the umbrella issue k8s-argocd#147. When #148 lands, open a follow-up
PR that updates `apps/automation/claude-bridge/externalsecret.yaml` to adopt the new circuit-breaker pattern. -->


## System-Wide Impact

- **Interaction graph:** Claude Code hooks (laptop, server-side scheduled agents) -> bridge HTTP API -> Postgres -> Discord Gateway -> Discord -> back to bridge via Gateway -> back to hook via polling. Bridge is the single source of truth.
- **Error propagation:** Hook fails closed on any bridge error path (unreachable, redaction-blocked, expired, denied, 5xx). Bridge fails closed on Postgres error (returns 5xx, hook treats as deny). Discord post failure stages outbox row, no auto-proceed.
- **State lifecycle risks:** Action state machine has terminal-once semantics enforced by DB. Outbox prevents auto-proceed without confirmed Discord post. Hash-chained audit log makes tampering detectable.
- **API surface parity:** Hook is one client. Remote scheduled agents are another, using the same `POST /notify` with their own API key. Slash commands are the operator surface. All three converge on the same approval state.
- **Integration coverage:** Cross-layer scenarios that unit tests will not prove: hook + bridge + Postgres + Discord round-trip; auto-proceed across pod restart; race between APPROVE and DENY across Gateway WebSocket; redaction block surfaced to ops channel.
- **Unchanged invariants:** Existing `discord-alert-proxy` continues to serve Alertmanager unchanged. Existing themed embed conventions preserved (color and emoji parity). Existing 1P ClusterSecretStore unchanged (new ExternalSecrets reference it). Existing TimescaleDB Postgres unchanged except for a new schema.

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 1P rate-limit regression from new ExternalSecrets | Medium | Medium | Coordinate with k8s-argocd#147/#148; adopt circuit breaker in Unit 12; manual ESO sync after rotation |
| SQLite-on-NFS class of corruption appearing in Postgres usage | Low | High | Postgres is the existing TimescaleDB instance with its own ops; no new persistence pattern introduced |
| Hook bypass via wrapper scripts | High | High | Documented as defense-in-depth, not primary gate; future PATH-shim work tracked separately; emergency override is loud and audited |
| Self-hosting circular dependency: cluster-down means bridge-down | Medium | High | Phase 0 hard-block hook works without bridge; bootstrap exemption for argocd ns; break-glass path documented |
| Discord Gateway WebSocket disconnect for >5 min | Medium | Medium | PrometheusRule alerts; discord.py auto-reconnect; fail-closed hook behavior on bridge unreachable |
| Server HMAC secret compromise | Low | Critical | Monthly rotation via 1P + ESO; in-place rotation invalidates all in-flight tokens (acceptable cost); audit-log hash chain detects forged entries |
| Allowlist user ID compromise (Discord account takeover) | Low | Critical | Discord 2FA mandatory for allowlisted user; rotation procedure documented; audit-log identifies actor on every decision |
| Redaction false positives blocking legitimate notifications | Medium | Low | Fail-closed surfaces blocks to ops channel; not silent; tune regex pack from observed false positives |
| Redaction false negatives leaking secrets to Discord chat | Medium | High | Multi-pattern regex pack; golden-file tests; periodic review against new secret formats; treat any leak as incident |
| Postgres outage takes the bridge with it | Medium | High | TimescaleDB is already monitored; bridge degrades to deny-all (correct fail-closed behavior); restore from snapshot |
| Audit log row tampering | Low | High | `claude_bridge_app` DB role lacks UPDATE/DELETE/TRUNCATE; BEFORE UPDATE/DELETE triggers as defense-in-depth; nightly GPG-signed pg_dump exported off-cluster, signing key on operator workstation only |
| Discord account takeover | Low | Critical | 2FA mandatory (operational); allowlist anomaly PrometheusRule fires on >5 approvals from one user in 10 minutes; HMAC token TTL caps blast radius per token; audit log captures every actor decision |
| Discord deplatforms the bot or sustained Gateway outage | Low | High | Phase 0 hard-block hook works without bridge; emergency override available in both hooks; single-vendor risk acknowledged; SMS/email fallback NOT in scope (deferred) |
| ArgoCD selfHeal races against Tier 2 undo for argocd app sync | High | Medium | Argocd app sync intentionally NOT supported as Tier 2 in initial release; ArgoCD changes go through Tier 3 with undo as Git revert PR |
| ESO circuit-breaker (#148) not ready when Phase 1 deploys | Medium | Medium | Bridge ExternalSecrets land without circuit breaker; rate-limit usage closely monitored; deferred follow-up retrofits when #148 merges |
| Discord IP range changes break NetworkPolicy egress | Medium | Medium | Documented review cadence (monthly); fallback: temporarily relax egress to wider range while updating policy; FQDN policies via Calico CRDs deferred |
| Phase 0 emergency override leaks via shell history | Low | High | Per-invocation env var only; explicit prohibition in break-glass.md against `.bashrc` storage; daily passphrase rotation; loud audit on every use |
| ArgoCD sync drift on the bridge's own manifests | Low | Medium | Bootstrap exemption documented; 2-person Git review on bridge PRs |
| Slash command response window (3s) exceeded under load | Low | Medium | All commands `defer()` immediately; SQLite/Postgres write happens after defer; alert on deferred-followup latency >10s |
| User sends `APPROVE` then `DENY` 200ms apart | Low | Low | Terminal-once semantics; second responder told who decided first |
| Phase 0 emergency override leaks the rotating passphrase | Low | High | Daily rotation; passphrase in 1P, accessed only via human reading; loud audit-log entry on every use |

## Phased Delivery

### Phase 0 (1 day) -- Hard floor
- Unit 1 only. No bridge dependency. Tier 3 commands hard-blocked from day one with emergency override available for break-glass scenarios.

### Phase 1 (3-5 days) -- Bridge skeleton + Tier 3 approval
- Unit 2: scaffolding and CI
- Unit 3: Postgres schema (no hash chain; least-privileged DB role)
- Unit 5: Discord client and slash commands (no token bucket)
- Unit 6: token lifecycle and state machine
- Unit 7: polling hook (sibling to hard-block, feature-flagged, emergency override available)
- Unit 10: deployment manifests, ESO, NetworkPolicy, ServiceMonitor, alerts (single replica, scope-separated API keys, CODEOWNERS, no portfolio skeleton)
- Outcome: Tier 3 commands flow through Discord with full approval semantics.

### Phase 1.5 (1 day) -- Hardening
- Unit 4: outbound redaction layer
- Outcome: defense-in-depth against accidental secret exposure to Discord.

### Phase 2 (1 day) -- Tier 2 outbox + auto-proceed + inline UNDO
- Unit 8: outbox, auto-proceed, inlined UNDO cases (no registry abstraction)
- Outcome: reversible actions can be queued for default-proceed with undo path.

### Phase 3 (half day) -- Daily digest
- Unit 9: digest cron and `/undo` command
- Outcome: every day's auto-proceeds are surfaced and undoable for 7 days.

### Phase 4 (half day) -- Runbooks
- Unit 11: runbooks (break-glass, bridge-down, decommission, architecture)
- Outcome: production-ready operational posture. Portfolio writeup deferred to +30 days.

### Deferred (separate tracker)
- ESO circuit-breaker retrofit (gated on k8s-argocd#148, tracked in umbrella k8s-argocd#147).
- Multi-replica Gateway with leader election (when measured demand justifies).
- FQDN-based egress NetworkPolicy via Calico CRDs (when broader CNI capability work happens).
- Image digest pinning fleet-wide (per the 04-19 Bitnami learning).
- Portfolio retrospective in `quasarlab-portfolio` (+30 days post-production).

## Documentation Plan

- `claude-bridge/README.md`: top-level overview, quickstart, link to architecture and runbooks.
- `claude-bridge/docs/architecture.md`: state diagram, token flow, redaction pipeline, threat model.
- `claude-bridge/docs/runbooks/`: break-glass, bridge-down, decommission.
- `claude-bridge/CLAUDE.md`: repo-specific Claude Code conventions.
- `k8s-argocd/docs/plans/2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md`: this document.
- `quasarlab-portfolio/projects/claude-bridge/`: skeleton on Phase 4, full writeup at +30 days.
- Memory updates after Phase 1 lands: new entries under `~/.claude/projects/-home-ladino/memory/k8s-argocd/` for any operational learnings.

## Operational / Rollout Notes

- **Rollout order:** Phase 0 hook in production immediately. Bridge to dev (the only real env) after Phase 1 unit tests pass. Live shadow mode for 48h: hook posts to bridge but bridge always returns "deny" so dangerous commands stay blocked while the system is exercised. Then enable approvals for one specific command class (e.g., `git push --force` only) for another 48h. Then enable the full deny list.
- **Monitoring:** Grafana dashboard with notify rate per tier, approval/denial rate, redaction-block rate, Discord Gateway connection status, Postgres pool usage, audit-log hash-chain integrity check (gauge, 1=ok, 0=mismatch). PrometheusRules listed in Unit 10.
- **Rotation cadence:** server HMAC secret monthly. Discord bot token rotated quarterly or on suspicion. Allowlist on user change. Break-glass passphrase daily.
- **Backup:** Postgres backed up via existing TimescaleDB backups. Audit log additionally exported nightly to TrueNAS dataset with snapshot retention.
- **Decommission:** follow `decommission.md`; confirm zero alerts fire afterward.

## Sources & References

- **Origin document:** none (planning bootstrap from a conversation transcript, no `docs/brainstorms/` entry exists).
- **Existing service template:** `infrastructure/monitoring/discord-alert-proxy/` in k8s-argocd.
- **Existing CI template:** `.github/workflows/build-discord-alert-proxy.yml` in k8s-argocd.
- **Existing ESO pattern:** `infrastructure/external-secrets/cluster-secret-store.yaml`.
- **Existing Prometheus wiring exemplars:** `apps/science/servicemonitor.yaml`, `apps/science/prometheus-rules.yaml`.
- **Active PRs:** k8s-argocd#147 (umbrella), k8s-argocd#148 (ESO circuit breaker, Phase 0).
- **Memory bank entries that bound this work:**
  - `~/.claude/projects/-home-ladino/memory/feedback_no_rollout_restart_arr.md`
  - `~/.claude/projects/-home-ladino/memory/k8s-argocd/2026-04-13_alerting_blackout.md`
  - `~/.claude/projects/-home-ladino/memory/k8s-argocd/2026-04-19_bitnami_images_removed.md`
  - `~/.claude/projects/-home-ladino/memory/k8s-argocd/1password-daily-rate-limit.md`
  - `~/.claude/projects/-home-ladino/memory/feedback_op_rate_limit_care.md`
  - `~/.claude/projects/-home-ladino/memory/feedback_eso_sync_after_1p_change.md`
  - `~/.claude/projects/-home-ladino/memory/project_2026-04-22_secrets_iac_rollout.md`
- **External docs:**
  - [Discord Gateway docs](https://docs.discord.com/developers/events/gateway)
  - [discord.py 2.6 docs](https://discordpy.readthedocs.io/en/stable/intents.html)
  - [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/)
  - [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
  - [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
  - [HashiCorp Vault Policies](https://developer.hashicorp.com/vault/docs/concepts/policies)
  - [LangGraph 2.0 interrupt](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt)
- **Anthropic Remote Tasks** is the user's strategic motivation, not a hard dependency.
