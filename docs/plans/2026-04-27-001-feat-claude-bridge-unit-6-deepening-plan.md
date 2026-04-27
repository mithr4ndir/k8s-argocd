---
title: "feat: claude-bridge Phase 1 Unit 6 deepening (token lifecycle + state machine + /approve, /deny, /status)"
type: feat
status: active
date: 2026-04-27
origin: docs/plans/2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md
deepened: 2026-04-27
target_repo: claude-bridge (service code) + k8s-argocd (manifests, this plan)
---

# feat: claude-bridge Phase 1 Unit 6 deepening

## Overview

Phase 1 Unit 6 turns the Discord slash command stubs shipped in Unit 5 into real approval flows. It is the security core of the bridge: HMAC token issuance and verification, single-shot nonce consumption, the action state machine with terminal-once semantics, allowlist enforcement at the slash command layer, append-only audit log writes for every state transition, dual-secret HMAC rotation, and best-effort embed updates with outbox-backed retry.

This deepening pass resolves seven open design questions surfaced during ce:plan, plus five additional concerns discovered while reading the current codebase. The original Unit 6 stub in the master plan stays the canonical record of what changes; this document captures the *how* and *why* one level deeper, suitable for handing to an implementer without further discussion.

## Problem Frame

After Unit 5, the slash commands `/approve`, `/deny`, `/status`, and `/undo` defer the interaction and reply "not yet implemented". POST /notify cannot be wired in because there is no token issuance, no verification path, no enforcement of the action state machine, and no allowlist enforcement at the slash command boundary. Until Unit 6 lands:

- The Phase 0 hard-block hook is the only gate. Unit 7 (polling hook) blocks on Unit 6.
- The audit log is empty -- no decisions, no verify failures, no replay attempts captured.
- Discord embed updates on decision are not implemented; the original Tier 3 request embed stays in `awaiting_response` shape forever.

Unit 6 closes all of the above for Tier 3 only. Tier 2 auto-proceed and Tier 1 status posts depend on the same building blocks but are scheduled separately (Units 8 and beyond).

## Requirements Trace

- R1. /approve and /deny atomically resolve a Tier 3 action; subsequent attempts return "already resolved" (see origin: master plan R1, R2).
- R2. Tokens are 22-char base64url, single-shot, 15-min TTL, HMAC-bound to action and requester (see origin).
- R3. State transitions are validated by an explicit transition table AND by a row-level optimistic-lock UPDATE in the same transaction as the audit_log INSERT.
- R4. Allowlist is enforced at /approve and /deny; non-allowlisted users receive a generic "not authorized" reply and the attempt is audit-logged.
- R5. /status returns full state to allowlisted users and a generic "no action visible" reply to others, regardless of whether the action_id exists.
- R6. HMAC secret rotation is zero-downtime via dual-secret window matching the 15-min token TTL.
- R7. Postgres outage during /approve fails closed with a typed degradation reply; no client-side caching, no double-spend risk.
- R8. Embed edit on decision is best-effort with outbox-backed retry; the slash command always replies promptly.
- R9. POST /notify is idempotent on an optional client-supplied `client_request_id`.
- R10. Every verify failure category is captured in audit_log; Discord reply collapses categories to two messages (`invalid or expired`, `already resolved`).

## Scope Boundaries

- Tier 3 approval flow only. Tier 2 outbox + auto-proceed is Unit 8.
- /undo handler stays a stub (Unit 9). Unit 6 only ensures /undo can read the action_id parameter and reply.
- POST /notify endpoint and its API key middleware ARE in scope (Unit 6 needs /notify to issue tokens that /approve verifies). Rate limiting on /notify is deferred to Unit 7.
- Hook side (PreToolUse polling) is Unit 7; this plan stops at the bridge's API surface.

### Deferred to Separate Tasks

- Tier 2 outbox worker + auto-proceed countdown: Unit 8 in master plan.
- /undo replay: Unit 9.
- Read/write split allowlist (deputy operator who can /status but not /approve): not implemented; documented as a future consideration.
- Per-action HMAC key derivation (so rotation only affects new tokens): rejected as over-engineered for one operator.
- Hash-chain audit log: rejected in master plan; not revisited.
- Postgres-side transition trigger: rejected; primary control is optimistic-lock UPDATE plus application transition table, with a CHECK on `prev_state NOT IN <terminal>` as cheap defense-in-depth.

## Context & Research

### Relevant Code and Patterns

- `claude-bridge/src/claude_bridge/persistence/models.py` -- already defines `Action`, `Nonce`, `AuditLog`, `Outbox`, `AllowlistEntry`, `UndoDecision`. Token vocabulary lives in `ACTION_STATES`, `NONCE_STATUSES`, etc. Unit 6 adds one column (`actions.client_request_id`) and one CHECK constraint (`prev_state NOT IN <terminal>`).
- `claude-bridge/src/claude_bridge/persistence/db.py` -- asyncpg pool plus DatabaseSettings. /healthz already checks pool health; /readyz extends with a new typed reason value.
- `claude-bridge/src/claude_bridge/bot/client.py` -- BridgeBot with handler methods (`handle_approve`, `handle_deny`, `handle_status`, `handle_undo`). Unit 6 fills these in. Methods stay on BridgeBot and delegate to a domain-layer `ApprovalService` so the test surface is isolated from discord.py.
- `claude-bridge/src/claude_bridge/bot/embeds.py` -- `build_decision_confirmation_embed` already exists. Unit 6 uses it for the followup reply on /approve and /deny. Adding two new embed builders for the resolved-state edits (`build_resolved_request_embed_approved`, `build_resolved_request_embed_denied`) keeps the original request embed correlatable but visually clearly resolved.
- `claude-bridge/src/claude_bridge/bot/settings.py` -- DiscordSettings is the place to add `hmac_secret_current` and `hmac_secret_previous` (SecretStr fields, env-var sourced).

### Institutional Learnings

- **Single-replica Discord Gateway** (memory: `feedback_single_replica_discord_gateway.md`): the Tier 3 approval embed has exactly one channel_message_id; multi-replica would race the edit. Unit 6 assumes single-replica and does not need leader election.
- **Audit log is signed-export, not hash-chain** (memory: `feedback_audit_log_signed_export_not_chain.md`): Unit 6's audit log writes go to the existing append-only `audit_log` table. No chain.
- **API key scope separation** (memory: `feedback_api_key_scope_separation.md`): Tier 3 from a `remote-agent-*` key is rejected at /notify with 403. Unit 6 enforces this on the issuance side, NOT the verification side -- by the time we get to /approve, the actor_source has already been recorded.
- **`from __future__ import annotations` + discord.py annotation resolver** (Unit 5 hardening pass, 2026-04-27): never alias `app_commands.Range[str, N, N]` to a local variable; inline it directly in the signature. Unit 6 keeps the same pattern in any new command parameters.

### External References

- Master plan: `docs/plans/2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md` (sections: Approval state machine, Approval token issuance and verification, Hook -> Bridge flow, Implementation Unit 6).
- discord.py 2.6 docs for `Message.edit` and `channel.fetch_message` rate limits and 404 semantics.
- OWASP Cryptographic Storage Cheat Sheet -- HMAC-SHA256 server secrets, constant-time compare via `hmac.compare_digest`.
- Python `secrets.token_urlsafe(16)` -- 22-char base64url output, 128-bit entropy.
- asyncpg docs on `connection.execute` returning `'UPDATE 0'` vs `'UPDATE 1'` for optimistic-lock detection.

## Key Technical Decisions

- **Token verification ordering**: shape-check the raw token first (length + base64url charset) to fail fast on garbage. Then SHA-256 the raw bytes and load the nonce row by `token_hash` PK. Then HMAC-verify against `(server_secret, raw_bytes || canonical(payload_from_row))` in constant time using `hmac.compare_digest`. Then run the atomic state-transition UPDATE. **The HMAC payload is read from the nonce row, not embedded in the token, because the token is intentionally short (22 chars) for mobile UX.** A bogus token costs one indexed PK lookup; an attacker probing random tokens cannot extract information beyond "exists or not" via timing because the constant-time compare and the early shape check normalize the path.
- **Discord error-message collapse**: every verify failure category collapses to one of two ephemeral replies (`invalid or expired` or `already resolved by <user> at <ts>`). The audit log records the precise category. This denies an attacker the ability to distinguish `invalid_hmac` from `replayed` from `mismatched` while preserving full forensic detail.
- **Burn-on-first-use, not soft burn**: Tier 3 is irreversible. The first /approve OR /deny atomically consumes the nonce and transitions state. Subsequent attempts (including from the same user) return "already resolved by <user> at <ts>" and the attempt is audit-logged as a duplicate. Soft burn (allow /deny after /approve) is rejected because the bridge has already returned approval to the polling hook by then.
- **State machine: app-level table + DB-level optimistic lock + DB-level terminal CHECK**. Three layers, each catching a different class of bug. The transition table in `state_machine.py` is the readable specification. The `UPDATE actions SET state=... WHERE action_id=$1 AND state=$2 RETURNING action_id` is the runtime guard. The `CHECK (prev_state NOT IN <terminal>)` constraint catches manual SQL mistakes. NO trigger on `actions` -- that would block legitimate incident-time admin recovery.
- **HMAC dual-secret rotation, 15-min overlap matching token TTL**. Settings exposes `hmac_secret_current` and `hmac_secret_previous` (both SecretStr). Verify tries current first, falls back to previous in constant time. Rotation playbook: write new value to `current` field of 1P item, copy old `current` to `previous`, kick ESO sync, wait 16 minutes, clear `previous`. Audit log records `hmac_secret_slot` (`current` or `previous`) for every successful verify so post-rotation forensics know which slot signed each token.
- **Postgres outage: fail closed, no fallback storage**. /readyz returns 503 with `reason=postgres_pool_degraded`. Slash command replies "Bridge cannot verify right now (transient). Token still valid; retry in 60 seconds." No client-side cache. On bridge restart, a startup reconciliation walks `actions` in `awaiting_response` past their `ttl_expiry` and transitions them to `expired` with audit-log entry `actor='reconciliation', actor_source='local'`.
- **Embed edit is best-effort with outbox retry**. /approve handler (1) defers ephemerally, (2) verifies + state-transitions + audit-logs in one transaction, (3) replies to the slash command interaction with `build_decision_confirmation_embed` immediately, (4) attempts the embed edit inline; on failure or 404 (message deleted), inserts an `outbox` row with `notify_idempotency_key='<action_id>:decision-edit'` so the Unit 8 outbox worker drains the retry queue. Slash command never blocks on Discord-side embed update.
- **/status leakage policy**: non-allowlisted user gets a generic "no action visible" reply; allowlisted user gets state, decided_by, decided_at, ttl_expiry. Never returns the token or the raw command. Both code paths audit-log the `/status` attempt with the requester's user_id (anomaly detection signal: a non-allowlisted user enumerating action_ids).
- **Idempotent /notify on `client_request_id`**: new optional column `actions.client_request_id TEXT NULL` with a partial unique index scoped to ACTIVE states only: `CREATE UNIQUE INDEX idx_actions_client_request_id_active ON actions (client_request_id) WHERE client_request_id IS NOT NULL AND state IN ('pending','pending_notify','awaiting_response','committing')`. The active-states predicate is required: a non-scoped `WHERE client_request_id IS NOT NULL` index would block the retry-after-expiry path because the original (now-terminal) row would still occupy the key. With the scoped predicate, terminal/expired actions release the key and a fresh /notify with the same client_request_id can land. POST /notify lookup query: `SELECT action_id FROM actions WHERE client_request_id=$1 AND state IN (<active>) ORDER BY created_at DESC LIMIT 1`. If found, return existing action_id and issue a fresh nonce (audit row `event='notify_retry_reissue'`). If not found (no active row), insert a new action; the unique index does not conflict because terminal rows are excluded. The set of "active states" is centralized in `state_machine.py` as `ACTIVE_STATES` to keep the index predicate, the lookup query, and the application logic in lockstep; adding a new state requires updating all three together (caught by the graph-completeness test in 6.3).
- **Audit `redacted_payload` minimum schema**: typed via a Pydantic model `AuditEventPayload` enforced on write, JSONB-tolerant on read so older shapes don't break replay. Fields: `event`, `action_id`, `decided_by`, `actor_source`, `verify_failure_reason`, `hmac_secret_slot`, `client_request_id`, `ts_iso`.

## Open Questions

### Resolved During Planning

- **Token verification ordering**: shape -> nonce row lookup -> HMAC verify -> state UPDATE. See Key Technical Decisions.
- **Burn-on-first-use vs soft-burn**: strict burn. See decisions.
- **State machine location**: app + DB optimistic lock + DB CHECK on prev_state. See decisions.
- **HMAC rotation**: dual-secret, 15-min overlap, current+previous slots. See decisions.
- **Postgres outage**: fail closed with typed reason, no fallback. See decisions.
- **/status to non-allowlisted**: generic reply, audit-logged. See decisions.
- **Embed edit timing**: best-effort + outbox retry. See decisions.
- **Idempotent /notify**: optional `client_request_id` with partial unique index scoped to ACTIVE states only (so retry-after-expiry can land a new row). See decisions.
- **Audit payload schema**: Pydantic model enforced on write, tolerant on read. See decisions.
- **Discord error message collapse**: two ephemeral replies; full categories in audit log. See decisions.
- **/notify rate limiting**: deferred to Unit 7.

### Deferred to Implementation

- Exact text of the two Discord ephemeral replies (UX wording; needs to match LOTR/Star Wars theming established in `embeds.py` flavors).
- Whether to surface `hmac_secret_slot` in the Discord embed footer (probably not -- forensic-only) or keep it audit-log-only.
- Exact backoff schedule for outbox-driven embed retry (Unit 8 will own; Unit 6 just inserts the row).
- Whether the startup reconciliation pass should run as part of the FastAPI lifespan or as a one-shot job. Default: lifespan.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Token verification ordering (revised)

```
verify(raw_token, claimed_user_id) -> VerifyResult:

  # Step 0: shape (no DB)
  if len(raw_token) != 22 or not all(c in BASE64URL for c in raw_token):
      audit('verify_failure', reason='shape')
      return VerifyResult(invalid_or_expired)

  # Step 1: nonce row by token_hash PK
  token_hash = sha256(raw_token.encode())
  row = SELECT * FROM nonces WHERE token_hash=token_hash
  if row is None:
      audit('verify_failure', reason='unknown_or_consumed')
      return VerifyResult(invalid_or_expired)

  # Step 2: HMAC over (raw_bytes || canonical(payload)) using current
  #         then previous secret. Constant-time compare in both branches.
  payload = canonical_json(row.payload)
  if not (compare_digest(hmac(secret_current, raw||payload), row.signed_sig)
       or compare_digest(hmac(secret_previous, raw||payload), row.signed_sig)):
      audit('verify_failure', reason='invalid_hmac')
      return VerifyResult(invalid_or_expired)

  slot = 'current' if matched_current else 'previous'

  # Step 3: allowlist
  if claimed_user_id not in allowlist():
      audit('verify_failure', reason='unauthorized', user=claimed_user_id)
      return VerifyResult(unauthorized)  # rendered as "not authorized" to user

  # Step 4: expiry
  if now() > row.expiry:
      audit('verify_failure', reason='expired')
      return VerifyResult(invalid_or_expired)

  # Step 5: atomic single-shot consume + state transition in one tx
  with tx:
      consumed = UPDATE nonces SET status='consumed'
                 WHERE token_hash=$1 AND status='pending' RETURNING token_hash
      if consumed empty:
          audit('verify_failure', reason='replayed')
          return VerifyResult(already_resolved, decided_by=row.action.decided_by)

      transitioned = UPDATE actions SET state=NEW, prev_state=current,
                       decided_by=user, decided_at=now()
                     WHERE action_id=$1 AND state='awaiting_response' RETURNING action_id
      if transitioned empty:
          audit('verify_failure', reason='state_race')
          return VerifyResult(already_resolved)

      audit('approve' or 'deny', action_id, decided_by, hmac_secret_slot=slot)

  return VerifyResult(ok, ...)
```

Note: `row.signed_sig` is stored at issuance time (the issuer computes HMAC and persists the signature in the nonce payload JSONB). We compare against this stored signature, NOT against a fresh recomputation, so the verify path does not have to re-derive the canonical payload bytes from typed columns.

### State transition table

```
ALLOWED_TRANSITIONS = {
    "pending":            {"pending_notify"},
    "pending_notify":     {"awaiting_response", "redaction_blocked", "notify_failed"},
    "awaiting_response":  {"committing", "expired", "denied"},
    "committing":         {"approved", "commit_failed"},
    # terminal:
    "approved":           set(),
    "denied":             set(),
    "expired":            set(),
    "cancelled":          set(),
    "redaction_blocked":  set(),
    "notify_failed":      set(),
    "commit_failed":      set(),
    "auto_approved":      {"approved"},  # tier 2 only; flows through Unit 8
}
```

The DB CHECK constraint to add in this unit's migration:

```sql
ALTER TABLE actions ADD CONSTRAINT ck_actions_no_transition_from_terminal
  CHECK (prev_state IS NULL OR prev_state NOT IN
    ('approved','denied','expired','cancelled','redaction_blocked',
     'notify_failed','commit_failed'));
```

This is a property the application already maintains; the constraint catches manual SQL drift.

### Dual-secret HMAC rotation (timeline)

```
T=0    operator updates 1P field 'current' to NEW; field 'previous' = OLD
       ESO syncs into K8s Secret 'claude-bridge-hmac'
       Reloader restarts pod (or bridge SIGHUP re-reads /etc/secrets/ on hot path)
T+0..T+15min    bridge accepts tokens signed under either NEW or OLD
                tokens issued at T+ are signed under NEW only
T+15min         OLD-signed tokens have all expired (TTL=15min)
T+16min         operator clears 'previous' field; ESO syncs
T+next          pod restart picks up cleared 'previous' (or remains accepting NEW only)
```

Failure mode: if `previous` is left populated indefinitely, that's still safe -- it just means tokens signed under OLD remain accepted, which is moot because they all expired at T+15min. The ergonomic risk is forgetting to clear `previous` and rotating again later, ending up with 3 valid keys in your head. Documented in the rotation runbook.

### Slash command flow (Tier 3 /approve)

```
operator             BridgeBot                       Postgres                       Discord API
   |                    |                                |                              |
   |  /approve <token>  |                                |                              |
   |───────────────────►│                                |                              |
   |                    │ defer(ephemeral=True)          |                              |
   │                    ├────────────────────────────────────────────────────────────►│ (3s ack)
   │                    │ ApprovalService.approve(...)   |                              |
   │                    │   tokens.verify(raw, user)     |                              |
   │                    │     SELECT nonces by token_hash─►                             |
   │                    │     hmac.compare_digest(...)   |                              |
   │                    │     allowlist check            |                              |
   │                    │     UPDATE nonces consumed ────►                              |
   │                    │     UPDATE actions to approved ►                             |
   │                    │     INSERT audit_log ──────────►                             |
   │                    │     INSERT outbox(decision-edit)► (best-effort tail)         |
   │                    │   <- VerifyResult.ok           |                              |
   │                    │ followup.send(decision_embed)  |                              |
   │                    ├────────────────────────────────────────────────────────────►│
   │                    │ try edit Tier 3 request embed in bot-approvals               |
   │                    ├────────────────────────────────────────────────────────────►│ (best-effort)
   │                    │ on edit fail: log warning; outbox row already queued          |
   │  ephemeral reply   │                                |                              |
   │◄───────────────────┤                                |                              |
```

## Implementation Units

This deepening pass keeps the master plan's Unit 6 boundary intact (one PR, one feature branch). The work below is the unit's internal sequencing -- *steps* an implementer takes inside Unit 6, not new top-level units.

- [ ] **6.0: Migration -- schema additions**

**Goal:** Add the columns, constraints, and indexes that the rest of Unit 6 needs.

**Requirements:** R3, R9.

**Dependencies:** Unit 3 schema is in place.

**Files:**
- Create: `src/claude_bridge/persistence/migrations/versions/0002_unit_6_token_lifecycle.py`
- Modify: `src/claude_bridge/persistence/models.py` (add `actions.client_request_id` Mapped column + the new CHECK)
- Test: `tests/integration/test_unit_6_migration.py`

**Approach:**
- Add `actions.client_request_id TEXT NULL` plus a partial unique index scoped to active states: `CREATE UNIQUE INDEX idx_actions_client_request_id_active ON actions (client_request_id) WHERE client_request_id IS NOT NULL AND state IN ('pending','pending_notify','awaiting_response','committing')`. The state-list literal in the migration must match `state_machine.ACTIVE_STATES` exactly; the migration includes a comment pointing at that constant.
- Add the prev-state-not-terminal CHECK constraint.
- Re-issue the existing `claude_bridge_app` privilege grants for the new column (no change required; INSERT/UPDATE on actions are already granted).
- `alembic upgrade head` then `alembic downgrade -1` round-trips cleanly.

**Patterns to follow:**
- `0001_initial.py` for the migration shape, role grants, trigger creation.

**Test scenarios:**
- Happy path: migration applies on a fresh DB; the new index and constraint exist.
- Happy path: migration downgrades cleanly.
- Edge case: an existing action with `client_request_id=NULL` does not violate the new unique index.
- Edge case: inserting two ACTIVE actions with the same non-null `client_request_id` raises a unique violation.
- Edge case: inserting an active action with the same `client_request_id` as a TERMINAL row succeeds (the partial index excludes terminal rows). This proves the retry-after-expiry path lands.
- Edge case: transitioning the active row to `expired` and then inserting a fresh action with the same `client_request_id` succeeds (idempotency key released by terminal transition).
- Error path: attempting to UPDATE an action whose prev_state is `'approved'` raises the new CHECK constraint.
- Integration: the migration is idempotent (applying it twice via `alembic stamp` then `alembic upgrade` is a no-op).
- Integration: `state_machine.ACTIVE_STATES` matches the literal state list in the migration's index predicate (assert via Python introspection in a unit test so a future state addition that updates only the constant is caught at test time, not in production).

**Verification:**
- `alembic upgrade head` applied against the live `claude_bridge` DB on 192.168.1.123.
- `\d+ actions` shows the new column and constraint.

---

- [ ] **6.1: HMAC settings + dual-secret loader**

**Goal:** Wire `hmac_secret_current` and `hmac_secret_previous` through DiscordSettings so domain code can call a typed accessor that hides slot fallback.

**Requirements:** R6.

**Dependencies:** 6.0.

**Files:**
- Modify: `src/claude_bridge/bot/settings.py` (add two `SecretStr | None` fields under env prefix `CLAUDE_BRIDGE_HMAC_`)
- Create: `src/claude_bridge/domain/hmac_secret.py` (typed accessor + bytearray storage + clear-on-shutdown helper)
- Test: `tests/unit/test_hmac_secret.py`

**Approach:**
- Settings fields: `hmac_secret_current: SecretStr | None`, `hmac_secret_previous: SecretStr | None`.
- Validator: when `enabled=True` (or a separate `hmac_enabled` switch), `hmac_secret_current` MUST be set; `hmac_secret_previous` is optional.
- `hmac_secret.py` exposes `class HmacSecretBox` holding two `bytearray`s. On shutdown, overwrite each with zero bytes (best-effort; CPython interns nothing about a bytearray's payload, so the wipe is meaningful).
- Loader reads from settings on FastAPI lifespan startup; the box is stored on `app.state.hmac_box`.
- The verify routine takes the box and tries current first, then previous, both in constant time.

**Patterns to follow:**
- `bot/settings.py` validator pattern.
- `bytearray` mutation per master plan decision on key residency.

**Test scenarios:**
- Happy path: box.verify(raw, payload, signature) returns (True, 'current') when signature matches current.
- Happy path: returns (True, 'previous') when signature matches previous and current does not.
- Edge case: returns (False, None) when neither matches; both compare paths still ran (prove via timing assertion is not feasible in unit tests, so settle for behavioral assertion that both attempts were made).
- Edge case: `hmac_secret_previous` is None; verify against current only; no NoneType errors.
- Edge case: settings load with empty SecretStr `current` raises at startup (defense-in-depth like the bot_token validator).
- Edge case: shutdown wipes box; subsequent verify raises a clear `BoxClosedError`.
- Integration: lifespan-managed box: app startup -> box populated; app shutdown -> box wiped.

**Verification:**
- `mypy --strict` clean on the new module.
- `pytest tests/unit/test_hmac_secret.py` green.

---

- [ ] **6.2: Token issuance + verification (`tokens.py`)**

**Goal:** The pure-domain token module. No discord.py, no FastAPI. Pure functions plus an `ApprovalService` orchestrator added in 6.4.

**Requirements:** R2, R10.

**Dependencies:** 6.1.

**Files:**
- Create: `src/claude_bridge/domain/tokens.py`
- Create: `src/claude_bridge/domain/canonical.py` (canonical-JSON helper for the HMAC payload; deterministic key ordering)
- Test: `tests/unit/test_tokens.py`, `tests/unit/test_canonical.py`

**Approach:**
- `issue(action_id, command_hash, requester_id, expiry, secret_box) -> IssuedToken` returns the raw 22-char string and the row to INSERT into nonces. Caller (6.4) handles the INSERT.
- `verify(raw_token, claimed_user_id, db, secret_box, allowlist_fn) -> VerifyResult` follows the ordering in High-Level Technical Design.
- `VerifyResult` is a tagged union: `Ok(action_id, decided_state, slot)`, `InvalidOrExpired(reason: Literal[...])`, `Unauthorized()`, `AlreadyResolved(decided_by, decided_at)`.
- `hmac.compare_digest` for all signature comparisons; never `==`.
- `secrets.token_urlsafe(16)` for raw token generation; produces 22 chars.
- The HMAC payload is `(action_id, command_hash, requester_id, expiry_iso)` canonicalized via `canonical.dumps` (sorted keys, no spaces, ISO8601 timestamps).

**Execution note:** Test-first. The verify routine has 6+ failure paths and at least one critical race; characterization tests up front shape the implementation.

**Test scenarios:**
- Happy path: issue then verify returns Ok and consumes the nonce.
- Happy path: verify returns slot='current' when signed under current, slot='previous' when signed under previous.
- Edge case: token shape: 21 chars -> InvalidOrExpired('shape').
- Edge case: token shape: 22 chars but contains '=' -> InvalidOrExpired('shape').
- Edge case: token bound to user A, verified with user B -> Unauthorized (allowlist-based, not user-based; covered by allowlist test below).
- Edge case: token bound to action A, but the parent action has transitioned out of awaiting_response -> AlreadyResolved.
- Edge case: token signed under previous-rotated-out secret -> InvalidOrExpired('invalid_hmac') after 16 minutes.
- Edge case: same token verified twice from two coroutines -> exactly one Ok, one AlreadyResolved (race protection via UPDATE ... RETURNING).
- Edge case: token signed under current but tampered raw (e.g., last byte flipped) -> InvalidOrExpired('invalid_hmac').
- Edge case: clock skew on the issuer side: bridge is the only source of `expiry`, so this is by construction not testable; document why.
- Error path: HMAC over corrupted bytes -> InvalidOrExpired('invalid_hmac'); audit-log row with `verify_failure_reason='invalid_hmac'`.
- Error path: nonce row missing entirely -> InvalidOrExpired('unknown_or_consumed').
- Error path: allowlist read fails (DB outage during allowlist lookup) -> raise PostgresOutageError; caller (6.4) translates to the degradation reply.
- Integration: 100 concurrent verify calls on different tokens, each succeeds exactly once. (Property-based via Hypothesis if convenient; otherwise asyncio.gather.)
- Integration: Hypothesis property: random 22-byte tokens never verify (signed under no secret).

**Patterns to follow:**
- `secrets.compare_digest` and `secrets.token_urlsafe` per OWASP guidance.
- Tagged-union result pattern matching how Rust-style result types are commonly translated to Python (dataclasses + match statement).

**Verification:**
- `mypy --strict` clean.
- `pytest tests/unit/test_tokens.py tests/unit/test_canonical.py` green; race test green.

---

- [ ] **6.3: State machine module (`state_machine.py`)**

**Goal:** The transition table, the `transition(...)` function, and a small `enforce_terminal(state)` helper for early-exit checks.

**Requirements:** R3.

**Dependencies:** 6.0.

**Files:**
- Create: `src/claude_bridge/domain/state_machine.py`
- Test: `tests/unit/test_state_machine.py`

**Approach:**
- `ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]]` defined at module scope, lining up with `models.ACTION_STATES`.
- `def transition(db, action_id, from_state, to_state, **decision_fields) -> None`: validates the transition is in the table, then issues the optimistic-lock UPDATE in the caller's transaction; raises `IllegalTransitionError` (table check) or `ConcurrentModificationError` (RETURNING empty).
- `def is_terminal(state)`: returns bool.
- The transition function takes a `*reason: str` argument that is recorded in the audit log row by 6.4. It does not write the audit log itself -- it only does the UPDATE -- because the test surface stays minimal.

**Test scenarios:**
- Happy path: `pending_notify -> awaiting_response` succeeds; UPDATE returns the row.
- Happy path: `awaiting_response -> approved` with decided_by populated.
- Edge case: `pending -> approved` raises IllegalTransitionError (skipped a state).
- Edge case: `approved -> denied` raises IllegalTransitionError (terminal).
- Edge case: optimistic lock: concurrent transition where state has already moved -> ConcurrentModificationError.
- Edge case: `is_terminal('approved')` returns True; `is_terminal('awaiting_response')` returns False.
- Edge case: every state in `ACTION_STATES` is reachable from at least one other state OR is the initial state OR is terminal (graph-completeness check).
- Error path: unknown `to_state` -> IllegalTransitionError with a clear message.

**Verification:**
- `pytest tests/unit/test_state_machine.py` green.
- The graph-completeness test catches the case where someone adds a new state to `ACTION_STATES` without wiring transitions.

---

- [ ] **6.4: ApprovalService (orchestrator)**

**Goal:** The single domain entry point that the slash command handlers, the /notify endpoint, and the startup reconciliation pass all call into. Wraps tokens.py, state_machine.py, audit log writes, allowlist lookups, and the embed-edit best-effort hop.

**Requirements:** R1, R3, R4, R7, R8, R10.

**Dependencies:** 6.1, 6.2, 6.3.

**Files:**
- Create: `src/claude_bridge/domain/approval_service.py`
- Create: `src/claude_bridge/domain/audit.py` (typed `AuditEventPayload` Pydantic model + `write_audit(...)` helper)
- Create: `src/claude_bridge/domain/allowlist.py` (DB read + simple in-process cache invalidated on every write to `allowlist`)
- Test: `tests/integration/test_approval_service.py`, `tests/unit/test_audit.py`, `tests/unit/test_allowlist.py`

**Approach:**
- `ApprovalService.issue(notify_request) -> IssuedAction`: handles the /notify request. If `client_request_id` is supplied, looks for an ACTIVE row (state in `ACTIVE_STATES`) with that key; if found, returns the existing action with a freshly issued nonce (the partial unique index guarantees at most one active row per key). If no active row exists -- either no prior call OR the prior call's action has reached a terminal state -- inserts a new action (state=pending_notify) and a fresh nonce in one transaction. Terminal rows are silently bypassed by the partial index, so retry-after-expiry creates a new action without a UNIQUE_VIOLATION.
- `ApprovalService.approve(token, user_id) -> ResolveResult`: full /approve flow. Transactionally consumes nonce, transitions state, writes audit log. Returns a result that the caller (BridgeBot.handle_approve) translates to the Discord reply.
- `ApprovalService.deny(token, user_id) -> ResolveResult`: same shape as approve.
- `ApprovalService.status(action_id, user_id) -> StatusResult`: returns full state to allowlisted, generic to others. Always audit-logs the attempt.
- `ApprovalService.try_embed_edit(action_id, decided_state) -> None`: best-effort wrapper. Catches `discord.NotFound`, `discord.Forbidden`, `discord.HTTPException`. On non-retryable, logs warning; on retryable, inserts an outbox row with `notify_idempotency_key='<action_id>:decision-edit'`.
- `ApprovalService.reconcile_expired() -> int`: startup pass that walks `actions` in `awaiting_response` past `ttl_expiry` and transitions them to `expired` with audit-log entry.
- All DB work happens inside `async with conn.transaction():` so audit log INSERT and state UPDATE land or roll back together.

**Patterns to follow:**
- The existing `bot/embeds.py` `_safe_field` discipline for any caller-supplied strings echoed in the audit log payload.
- `asyncpg.Pool.acquire` + transaction context as already used in Unit 3.

**Test scenarios:**
- Happy path: approve flow end-to-end against testcontainers Postgres: token verifies, nonce consumed, action transitions, audit row appears.
- Happy path: deny flow end-to-end.
- Happy path: status from allowlisted user returns full payload; from non-allowlisted returns generic; both audit-log.
- Edge case: approve when DB is healthy then connection killed mid-transaction -> rolls back; nonce stays pending; action state unchanged.
- Edge case: approve raced against deny in two coroutines -> exactly one wins; loser receives AlreadyResolved with decided_by populated to the winner.
- Edge case: /notify retried with same client_request_id within TTL -> returns same action_id, fresh token, audit row `event='notify_retry_reissue'`.
- Edge case: /notify retried with same client_request_id AFTER ttl_expiry -> creates a new action (the old one expired); audit row reflects this.
- Edge case: try_embed_edit on a deleted message -> 404; logs warning, inserts outbox row.
- Edge case: reconcile_expired finds an awaiting_response action 2 hours past TTL -> transitions to expired, audit log records `actor='reconciliation'`.
- Error path: Postgres pool down during /approve -> raises PostgresOutageError; caller renders degradation reply; audit log NOT written (we cannot write).
- Error path: an action with state='approved' (terminal) reached via approve -> AlreadyResolved.
- Integration: 50 parallel /approve attempts on the same valid token -> exactly one succeeds, 49 receive AlreadyResolved.
- Integration: one /approve from an allowlisted user, one /deny from another allowlisted user, both within 50ms -> exactly one terminal state; loser sees AlreadyResolved.

**Verification:**
- `pytest tests/integration/test_approval_service.py` green against testcontainers Postgres.
- `mypy --strict src/claude_bridge/domain/` clean.

---

- [ ] **6.5: Wire BridgeBot handlers + new embed builders + /readyz reason**

**Goal:** Replace the four stub bodies in `bot/client.py` with calls into ApprovalService. Add the two new embed builders. Extend /readyz with the new postgres_pool_degraded reason.

**Requirements:** R1, R5, R7, R8.

**Dependencies:** 6.4.

**Files:**
- Modify: `src/claude_bridge/bot/client.py` (handle_approve, handle_deny, handle_status; handle_undo stays a stub but logs to audit)
- Modify: `src/claude_bridge/bot/embeds.py` (add `build_resolved_request_embed_approved`, `build_resolved_request_embed_denied` -- variants of the original Tier 3 request embed showing the resolved state)
- Modify: `src/claude_bridge/main.py` (extend ReadyzResponse `reason` Literal to include `postgres_pool_degraded`; lifespan creates ApprovalService and stores on app.state)
- Test: `tests/unit/test_bot_client.py` (extend handler tests with mocked ApprovalService), `tests/unit/test_bot_embeds.py` (new embed shapes)

**Approach:**
- handle_approve: defer ephemeral, call `app.state.approval_service.approve(token, str(interaction.user.id))`, render reply per ResolveResult shape.
- handle_deny: same shape with deny.
- handle_status: defer ephemeral, call `approval_service.status(action_id, str(interaction.user.id))`, render reply.
- handle_undo: defer ephemeral, audit-log the attempt with `event='undo_stub_invoked'`, reply "Undo is not yet implemented (Phase 1 Unit 9)."
- The two new embed builders take the resolved state plus the original embed fields and return a new Embed. The caller (ApprovalService.try_embed_edit) does `message.edit(embed=...)`.
- /readyz: when `app.state.bot is None or not bot.is_ready()` -> existing `discord_gateway_not_connected`. When pool unhealthy -> new `postgres_pool_degraded`. When both unhealthy -> the more severe reason wins (postgres first; without DB the bot can't do anything useful).

**Test scenarios:**
- Happy path: handle_approve with mocked ApprovalService returning Ok renders the decision_confirmation_embed reply ephemerally.
- Happy path: handle_deny with mocked ApprovalService returning Ok renders the deny variant.
- Happy path: handle_status from allowlisted -> renders state, decided_by, decided_at; does not contain command or token.
- Edge case: handle_status from non-allowlisted -> renders generic "no action visible" regardless of whether action_id exists.
- Edge case: handle_approve receives PostgresOutageError -> ephemeral degradation reply; no Discord interaction failure.
- Edge case: handle_approve receives Unauthorized -> ephemeral "not authorized" reply; audit-logged.
- Edge case: handle_approve receives AlreadyResolved -> ephemeral "already resolved by <user> at <ts>" reply.
- Edge case: build_resolved_request_embed_approved produces a Discord-valid Embed with green color and the resolved-state badge.
- Edge case: build_resolved_request_embed_denied produces a Discord-valid Embed with denied-gray color.
- Integration: end-to-end /approve via mocked discord.Interaction asserts ApprovalService called, ephemeral followup sent, embed-edit attempted, no panics on edit failure.

**Verification:**
- All existing Unit 5 tests still green.
- `pytest tests/unit/test_bot_client.py tests/unit/test_bot_embeds.py` green with new tests.
- Manual: in a test guild, exercise /approve, /deny, /status with valid and invalid inputs.

---

- [ ] **6.6: POST /notify endpoint + API key middleware**

**Goal:** The HTTP entry point that issues tokens. Single route, API-key gated, scope-separated by key prefix.

**Requirements:** R2, R9, R10.

**Dependencies:** 6.4.

**Files:**
- Create: `src/claude_bridge/api/__init__.py`, `src/claude_bridge/api/notify.py`, `src/claude_bridge/api/auth.py`
- Modify: `src/claude_bridge/main.py` (mount the API router; lifespan loads API keys from settings)
- Modify: `src/claude_bridge/bot/settings.py` (add `api_keys_local: list[SecretStr]`, `api_keys_remote: list[SecretStr]`; rename to `BridgeSettings` if Discord-only feels too narrow)
- Test: `tests/integration/test_notify.py`

**Approach:**
- API key middleware: extracts `X-API-Key` header, looks up against constant-time-compared known keys, sets `request.state.actor_source` ('local' or 'remote') from the matched list.
- POST /notify body: `tier: int (1..3)`, `command: str`, `requester: str`, `client_request_id: str | None`. Pydantic v2 model.
- Tier 3 from a `remote-agent-*` (request.state.actor_source='remote') key returns 403 immediately.
- Calls `ApprovalService.issue(...)`.
- Returns 202 Accepted with `{action_id, ttl_seconds, token}` for tier 3 (token is in body so the polling hook can pass it back); for tier 1/2 returns `{action_id, ttl_seconds}` only.

**Test scenarios:**
- Happy path: tier 3 with local key -> 202 with token.
- Happy path: tier 1 with remote key -> 202 without token (no approval needed; auto_approved path).
- Happy path: tier 2 with remote key -> 202 without token (auto_approved Unit 8 will handle countdown).
- Edge case: tier 3 with remote key -> 403 with body `{"error":"tier_3_requires_local_actor"}` (no token leaked).
- Edge case: same client_request_id replayed -> 202 with same action_id but a NEW token.
- Edge case: missing X-API-Key -> 401.
- Edge case: malformed JSON body -> 422.
- Edge case: tier=4 -> 422 (out of range).
- Error path: empty `command` -> 422.
- Error path: ApprovalService raises PostgresOutageError -> 503 with reason.
- Integration: end-to-end POST /notify -> /approve via slash command stub completes the flow.

**Verification:**
- `pytest tests/integration/test_notify.py` green.
- `curl -H 'X-API-Key: ...' -d '{...}' POST /notify` works in a manual test against a local bridge.

---

- [ ] **6.7: Reconciliation + lifespan wiring**

**Goal:** Add the startup reconciliation pass for orphan `awaiting_response` actions; wire ApprovalService creation into the lifespan; ensure the HMAC box is wiped on shutdown.

**Requirements:** R7.

**Dependencies:** 6.4, 6.5.

**Files:**
- Modify: `src/claude_bridge/main.py` (lifespan gains: HMAC box load + close; ApprovalService creation; reconciliation pass)
- Test: `tests/integration/test_lifespan_reconciliation.py`

**Approach:**
- On startup, after the DB pool is up, call `ApprovalService.reconcile_expired()`. Log how many it transitioned. If it raises (DB down at startup), the lifespan still proceeds (the bot can come up; reconciliation is best-effort).
- On shutdown, `app.state.hmac_box.close()` zeroes the bytearrays.
- ApprovalService is constructed once in the lifespan, stored on `app.state.approval_service`, reused by every handler.

**Test scenarios:**
- Happy path: lifespan startup calls reconcile_expired; no orphans -> 0 transitions; bridge comes up.
- Happy path: lifespan startup with one orphan -> transitions to expired, audit-logged.
- Edge case: DB down at startup -> reconcile fails; bridge still comes up; /readyz reports postgres_pool_degraded.
- Edge case: shutdown wipes the HMAC box; subsequent (test-only) verify call raises BoxClosedError.
- Integration: full lifespan up + /approve flow + lifespan down with a mid-flow nonce; nonce stays pending; next startup reconciles it as expired.

**Verification:**
- `pytest tests/integration/test_lifespan_reconciliation.py` green.
- Manual: running the bridge against a DB with 1 orphan logs the transition message at INFO.

## System-Wide Impact

- **Interaction graph:** the slash command handlers, /notify endpoint, and reconciliation pass all funnel through ApprovalService. No other module reads or writes `actions.state` directly. This single chokepoint is the principal property the unit relies on.
- **Error propagation:** PostgresOutageError raised inside ApprovalService propagates out to FastAPI / discord.py with a clean translation in each caller. Caller never sees raw asyncpg errors.
- **State lifecycle risks:** burn-on-first-use depends on the optimistic-lock UPDATE landing in the same transaction as the audit log INSERT. If anyone in 6.4 splits these into separate transactions, the audit log can be missing for a successful approval. Test 6.4's `approve when DB is healthy then connection killed mid-transaction -> rolls back` guards against this.
- **API surface parity:** /readyz now reports two reason values (`discord_gateway_not_connected`, `postgres_pool_degraded`). K8s liveness/readiness probes already branch on status code; dashboards must add the new reason.
- **Integration coverage:** the testcontainers Postgres suite is the only place that proves transaction atomicity across multiple writes. Unit tests with mocked DB cannot.
- **Unchanged invariants:** the audit_log table is still append-only via role grants + triggers; Unit 6 only INSERTs into it. The `claude_bridge_app` role still lacks UPDATE/DELETE/TRUNCATE on audit_log. The single-replica deployment posture is unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Optimistic-lock UPDATE in 6.4 split from audit log INSERT (separate transactions) creates orphaned audit gaps | Integration test asserts both writes happen or neither does, by killing the connection mid-transaction. |
| Token harvest via /notify probing | API key gate is the sole control; rate limit at /notify added in Unit 7 closes the residual concern. Tier 3 from remote keys is 403 today. |
| HMAC `previous` slot left populated forever after rotation | Documented in the rotation runbook. Audit log records `hmac_secret_slot` so an operator can grep for tokens still verified under `previous` past the 16-min mark and investigate. |
| Embed edit failure cascades into outbox flood | The outbox row has a fixed `notify_idempotency_key='<action_id>:decision-edit'` so idempotent retries cannot multiply. Unit 8's outbox worker enforces backoff. |
| /status enumeration from non-allowlisted users | Generic reply same in both branches; audit-log captures the requester and the queried action_id; PrometheusRule fires on >5 status calls in 10 min from non-allowlisted (deferred to Unit 10 alert pack). |
| client_request_id collision across tenants | Single-tenant homelab; documented as a known limitation. Adding a `tenant` column is deferred. |
| Postgres-side CHECK on prev_state blocks legitimate incident recovery (admin needs to UPDATE a terminal action manually) | Operator can `BEGIN; ALTER TABLE actions DROP CONSTRAINT ck_actions_no_transition_from_terminal; UPDATE ...; ALTER TABLE actions ADD CONSTRAINT ck_... CHECK (...); COMMIT;` -- documented in the bridge-down runbook. |

## Documentation / Operational Notes

- New 1Password item: `claude-bridge/server-hmac-secret` with two fields, `current` and `previous` (both 32+ char base64). ESO maps to K8s Secret `claude-bridge-hmac` with two keys.
- Rotation runbook (new doc, `claude-bridge/docs/runbooks/hmac-rotation.md`) covers the dual-secret swap timeline.
- Bridge-down runbook update: how to drop and recreate the prev_state CHECK in an incident.
- Memory bank update at the end of Unit 6: capture the verify-ordering decision, the dual-secret rotation pattern, and the burn-on-first-use rationale, since these will recur for any future authenticated-token surface.

## Sources & References

- **Origin document:** [docs/plans/2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md](2026-04-26-001-feat-claude-bridge-hitl-discord-plan.md) (Unit 6 stub at line 587; High-Level Technical Design at line 269).
- claude-bridge source today: PR #11 (Unit 5 Discord client), PR #12 (Unit 5 hardening pass).
- Memory: `feedback_single_replica_discord_gateway.md`, `feedback_audit_log_signed_export_not_chain.md`, `feedback_api_key_scope_separation.md`.
- External: OWASP Cryptographic Storage Cheat Sheet, Python `hmac` module docs, `secrets.token_urlsafe`, asyncpg transaction docs, discord.py 2.6 `Message.edit` rate limits.
