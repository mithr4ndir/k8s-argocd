# Requirements Document

## Introduction

Resume the paused ESO-side rate-limit mitigation work from the 2026-04-18 incident. ESO was scaled to 0 during the drain and then quietly scaled back to 1/1 once the cap recovered. The structural fix, a circuit breaker that stops ESO from retrying against an exhausted 1Password cap, was never shipped. This spec closes that gap.

The goal is a deterministic posture: if the 1P account daily cap is exhausted, ESO MUST stop calling `op` until the window resets. Today ESO keeps reconciling every `refreshInterval` + every ClusterSecretStore validation tick regardless, and each failing reconcile still counts against the cap. The mitigation in the runbook ("scale ESO to 0") is manual and easy to forget, and it loses all secret sync until someone remembers to scale back.

Scope is bounded to the External Secrets Operator deployment, the `onepassword-infrastructure` ClusterSecretStore, and the fleet of ExternalSecret resources that reference it. Alerting and quota observability already exist (spec `1password-quota-monitoring`, PR #109 + k8s-argocd monitoring PR), so this spec consumes those metrics rather than adding new ones.

This is a prerequisite for the `media-secrets-declarative` spec, which will add ~7 new ExternalSecret resources across the media namespace and would amplify the drain risk if shipped first.

## Alignment with Product Vision

Homelab prime directive: everything must be managed in code (IaC), no manual configuration. The current "scale ESO to 0 by hand during an incident" response violates that directive. This spec makes the rate-limit protection declarative and enforced by Kubernetes itself, consistent with the existing ansible-side kill switch.

Security-first directive section "API Security" requires rate limiting and throttling on all public endpoints with exponential backoff. ESO is a consumer of a rate-limited external API (1Password) and today implements none of those controls. This spec brings ESO into line with the house standard.

## Requirements

### Requirement 1: ExternalSecret refresh interval standardization

**User Story:** As an operator, I want every ExternalSecret in the cluster to declare a refresh interval appropriate to the secret's sensitivity, so that the fleet's steady-state 1P read rate is predictable and known.

#### Acceptance Criteria

1. WHEN any ExternalSecret is authored or modified THEN it SHALL declare a non-zero `spec.refreshInterval`.
2. WHEN the fleet is audited THEN the default refresh interval SHALL be 24h for all long-lived secrets (API keys, DB passwords, TLS certs that do not rotate on minutes).
3. WHEN a secret has a real rotation SLO shorter than 24h THEN its ExternalSecret SHALL document the rotation reason in an inline comment and use the matching interval (e.g. 1h for short-lived tokens).
4. WHEN the cluster is observed THEN the total projected 1P read rate from ExternalSecrets SHALL be under 100 reads/day at steady state, leaving 90% of the daily cap available for ad-hoc and ansible work.
5. WHEN a new ExternalSecret is opened in a PR THEN CI SHALL fail if `spec.refreshInterval` is absent or set to `0`.

### Requirement 2: ESO circuit breaker against an exhausted cap

**User Story:** As an operator, I want ESO to stop calling `op` when the 1P daily cap is already exhausted, so that retries cannot extend the rate-limit window the way they did on 2026-04-18.

#### Acceptance Criteria

1. WHEN the metric `onepassword_ratelimit_remaining{type="account"}` is less than 10 THEN the ESO controller deployment SHALL be scaled to 0 replicas automatically.
2. WHEN that metric recovers to at least 100 THEN the ESO controller deployment SHALL be scaled back to 1 replica automatically.
3. WHEN the circuit breaker trips or recovers THEN a Discord alert SHALL fire with a LOTR theme so it is recognizable against the existing alert vocabulary.
4. WHEN ArgoCD attempts to reconcile ESO during a trip THEN it SHALL NOT override the 0-replica state (i.e. the scaling is reconciled by a controller ArgoCD respects, or the ArgoCD Application ignores the `replicas` field).
5. WHEN the cluster boots from cold and the metric is unavailable THEN the circuit breaker SHALL default to "closed" (ESO runs) rather than "open", so an outage of the collector cannot itself stop secret sync.

### Requirement 3: ClusterSecretStore validation cost reduction

**User Story:** As an operator, I want ESO's ClusterSecretStore health checks to not count as meaningful reads against the 1P cap, so that a large fleet of ExternalSecrets does not multiply validation cost.

#### Acceptance Criteria

1. WHEN the ClusterSecretStore is reconciled THEN it SHALL NOT issue an `op` vault-item call per reconcile loop merely to validate credentials.
2. WHEN the ClusterSecretStore fails validation THEN it SHALL back off with exponential jitter up to a 1h cap, not retry every 7 minutes as observed on 2026-04-18.
3. WHEN multiple ExternalSecrets reference the same store THEN the store SHALL be validated once per refresh window, not once per ExternalSecret.

### Requirement 4: Scheduled drain window ("night mode")

**User Story:** As an operator, I want ESO to do its daily refresh in a concentrated window rather than spreading per-ExternalSecret ticks across the clock, so that ad-hoc `op` usage during business hours has a predictable quota head-room.

#### Acceptance Criteria

1. WHEN the fleet is running at steady state THEN at least 80% of all ExternalSecret refreshes SHALL be scheduled during a single 1-hour window overnight local time (US/Pacific).
2. WHEN new ExternalSecrets are added THEN they SHALL inherit the same window unless the PR explicitly justifies otherwise.
3. WHEN the window fires THEN the `onepassword_ratelimit_used{type="account"}` metric SHALL visibly step up, providing a natural "heartbeat" observable on the Grafana panel.

### Requirement 5: Documentation and runbook update

**User Story:** As an on-call operator, I want the existing quota runbook updated to reflect the new automated controls, so that the manual "scale ESO to 0" step is retired and operators know which metric drives the circuit breaker.

#### Acceptance Criteria

1. WHEN the runbook `docs/runbooks/onepassword-quota.md` is updated THEN it SHALL remove the manual scale-to-0 instruction and replace it with circuit-breaker status verification.
2. WHEN an operator is investigating an unexpected circuit-breaker trip THEN the runbook SHALL include the three-step diagnostic: confirm quota via `op service-account ratelimit`, inspect circuit-breaker state, inspect recent ExternalSecret errors.
3. WHEN the spec ships THEN the project's `CLAUDE.md` (k8s-argocd) SHALL be updated to document the circuit-breaker contract so future agents do not try to manually scale ESO again.

## Non-Functional Requirements

### Code Architecture and Modularity

- The circuit breaker SHALL be implemented as a small, testable piece: either a ConfigMap-driven CronJob + kubectl scale, a KEDA ScaledObject consuming the Prometheus metric, or a purpose-built controller. Prefer the smallest solution that meets Requirement 2.
- The circuit breaker SHALL live in `infrastructure/external-secrets/` alongside the existing ESO manifests, not in a new top-level path.
- Alerting SHALL reuse the existing Discord webhook + LOTR theme catalog, no new routing.
- Manifests SHALL be kustomize-native; no new Helm dependencies.

### Performance

- Circuit breaker reaction time SHALL be under 2 minutes from threshold crossing.
- Alert firing delay SHALL be under 30 seconds.

### Security

- The circuit breaker SHALL NOT require a new 1P service-account token.
- The metric scrape target SHALL be read-only Prometheus, no write path.
- The Kubernetes ServiceAccount doing the scaling SHALL have least-privilege RBAC: `patch deployments/scale` in the `external-secrets` namespace only, no other verbs or namespaces.

### Reliability

- Default-closed: if the quota metric is unavailable for any reason, ESO SHALL continue to run. False positives (shutting off secrets during a monitoring outage) are worse than false negatives.
- The circuit breaker SHALL emit a heartbeat metric so "breaker evaluated recently" is observable, independent of whether it tripped.

### Usability

- Tripping state SHALL be visible on the existing 1Password Grafana dashboard (new single-stat panel) without a separate dashboard.
