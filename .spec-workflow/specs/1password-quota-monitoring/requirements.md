# Requirements Document

## Introduction

Add observability for the 1Password service account daily rate limit so we get a loud, actionable signal before we exhaust the quota instead of discovering it via an outage. The 2026-04-18 incident pinned the quasarlab 1Password account's 1000-request daily cap for ~24 hours, silently breaking every ExternalSecret sync and ansible playbook run that needs secrets. The rate limit was invisible until we found the undocumented `op service-account ratelimit` command.

The feature is a Prometheus-scrapable metric source, a set of alert rules, and a Grafana panel covering hourly-per-token and daily-per-account usage. It uses the existing node_exporter textfile collector pattern already in use for ansible metrics, so no new scrape target is required.

## Alignment with Product Vision

The homelab's prime directive is "everything must be managed in code (IaC), no manual configuration." That extends to operational visibility: if a service limit can silently fail the cluster, we must know about it before it does. This spec closes the last visibility gap in the 1Password defense-in-depth series that already includes the kill switch (PR #104), secret caching (PR #105-#106), and 24h ESO refresh (PR #131).

## Requirements

### Requirement 1: Collect 1Password quota usage on a schedule

**User Story:** As an operator, I want the current 1Password daily and hourly rate-limit usage exposed as Prometheus metrics, so that I can see real-time utilization on Grafana and set alert thresholds on it.

#### Acceptance Criteria

1. WHEN the quota collector runs THEN it SHALL call `op service-account ratelimit` once and parse its tabular output into labeled metrics.
2. WHEN the collector runs THEN it SHALL write the metrics as a Prometheus textfile in the existing `/var/lib/node_exporter/textfiles/` directory so the existing node_exporter on command-center1 scrapes them.
3. WHEN `op` is unavailable, the token is unset, or the rate limit is already exhausted (the ratelimit command itself can be blocked) THEN the collector SHALL preserve the last successful metric values, write an `onepassword_ratelimit_collector_success` gauge of `0`, and exit cleanly with a syslog line but not an error code.
4. WHEN the kill switch (`/var/lib/ansible-quasarlab/1p-killswitch`) is active THEN the collector SHALL skip calling `op` to avoid extending the rate-limit window, but still refresh the `onepassword_ratelimit_collector_success` metric to `0` with a distinct syslog reason.
5. WHEN the collector runs successfully THEN it SHALL emit at minimum: `onepassword_ratelimit_used`, `onepassword_ratelimit_limit`, `onepassword_ratelimit_remaining`, `onepassword_ratelimit_reset_seconds`, all labeled by `type` (`token|account`) and `action` (`read|write|read_write`).

### Requirement 2: Schedule the collector

**User Story:** As an operator, I want the quota collector to run often enough that alerts fire within minutes of hitting a threshold but not so often that it adds measurable load to the daily cap itself.

#### Acceptance Criteria

1. WHEN the schedule is configured THEN it SHALL be a systemd timer on command-center1 firing every 15 minutes.
2. WHEN the collector runs THEN it SHALL consume exactly 1 request against the daily cap per firing (4/hour, 96/day = 9.6% of the 1000-daily cap).
3. WHEN the timer is deployed THEN it SHALL be idempotent via Ansible and survive host reboots.

### Requirement 3: Alert on quota exhaustion risk

**User Story:** As an operator, I want graduated alerts on approaching the 1Password daily cap so I can intervene (pause automation, upgrade plan) before secrets stop syncing.

#### Acceptance Criteria

1. WHEN the account daily `remaining` drops below 500 (50%) for 15 minutes THEN the system SHALL fire `OnePasswordQuotaHalfConsumed` at `info` severity.
2. WHEN the account daily `remaining` drops below 200 (20%) for 10 minutes THEN the system SHALL fire `OnePasswordQuotaLow` at `warning` severity with a runbook link.
3. WHEN the account daily `remaining` drops below 50 (5%) for 5 minutes THEN the system SHALL fire `OnePasswordQuotaCritical` at `critical` severity.
4. WHEN the account daily `remaining` equals 0 THEN the system SHALL fire `OnePasswordQuotaExhausted` at `critical` severity immediately.
5. WHEN `onepassword_ratelimit_collector_success == 0` for 30 minutes THEN the system SHALL fire `OnePasswordQuotaCollectorStale` at `warning` severity so we notice the collector itself has failed (distinct from "quota is fine, just not being measured").

### Requirement 4: Visualization

**User Story:** As an operator, I want a Grafana panel that shows current quota usage and recent trend so I can correlate consumption spikes with scheduled jobs.

#### Acceptance Criteria

1. WHEN the panel loads THEN it SHALL display the current `used / limit` ratio as a gauge for the account daily limit, the token hourly read limit, and the token hourly write limit.
2. WHEN the panel loads THEN it SHALL include a time-series showing `onepassword_ratelimit_used{type="account"}` over the last 24 hours with horizontal threshold lines at 50%, 80%, 95%.
3. WHEN the panel loads THEN it SHALL include a single-value indicator for `onepassword_ratelimit_reset_seconds` (time until reset).

## Non-Functional Requirements

### Code Architecture and Modularity

- The collector is a single shell script plus a small Python helper for the awk-of-the-table parse. It lives under `ansible-quasarlab/roles/op_quota_collector/` (role), not k8s. Ansible manages the systemd unit and timer. The alert rules and Grafana panel live in k8s-argocd (this repo).
- The script parses the CLI table output, not a JSON API, because `op service-account ratelimit` does not currently support `--format=json`. If 1Password adds JSON output in a future release, swap the parser with no downstream changes.
- No new runtime dependencies. `op` is already present on command-center1 (used by ansible wrappers). Python 3 stdlib only, no `pip` packages.

### Performance

- Collector total runtime target: under 5 seconds per firing, including the `op` HTTP roundtrip and textfile atomic rename.
- Each firing consumes exactly 1 request. No retries. If the call fails, the existing cached metric file is preserved and the next firing tries again.

### Reliability

- Textfile writes are atomic (write to `.tmp`, rename) so Prometheus never sees a partial file.
- The collector reuses the kill-switch library from `ansible-quasarlab/scripts/lib/op-killswitch.sh` so a rate-limit event during its own `op` call does not re-extend the window.
- On first deploy, the metric file does not exist; Prometheus scrape returns empty metrics and the alert rules correctly no-op until data arrives.

### Usability

- Operator runbook link on the warning/critical alerts points to `docs/runbooks/onepassword-quota.md` (to be created in k8s-argocd) with three actions: check usage, identify top consumers, pause offenders.
- Grafana panel is added to an existing dashboard (Infrastructure or Monitoring), not a new one, so it is discovered alongside related metrics.
