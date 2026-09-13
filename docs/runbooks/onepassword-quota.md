# Runbook: 1Password Quota Alerts

Applies to: `OnePasswordQuotaBurnRateHigh`, `OnePasswordQuotaHalfConsumed`, `OnePasswordQuotaLow`, `OnePasswordQuotaCritical`, `OnePasswordQuotaExhausted`, `OnePasswordQuotaCollectorStale`.

## Why you were paged

1Password Families plan enforces **two** rate limits. Our cluster hits the second one far more often than the first:

| Tier | Scope | Limit | Typical culprit |
|---|---|---|---|
| Per service account, hourly | Each SA token | 1000 reads/hr, 100 writes/hr | Runaway loop or cache miss storm |
| **Per account, daily** | **Across all SAs in the 1P account** | **1000 read_write/24h** | **Normal operation over weeks accumulates, ESO retry loop, interactive `op` testing** |

The alerts in this group watch the **daily account cap** because that is the one that causes cluster-wide outages. The 2026-04-18 incident pinned it for 24h and silently broke every `ExternalSecret` sync plus every ansible play that needed a secret.

## Confirm current state

Run from a host with the `op` CLI and token (command-center1 is canonical). `op service-account ratelimit` is a control-plane call that does NOT count against any of the three rate-limit tiers, so probe as often as you need during an incident (verified 2026-04-19 against a draining cap).

```bash
ssh command-center1
OP_SERVICE_ACCOUNT_TOKEN="$(cat ~/.config/op/service-account-token)" \
  timeout 30 op service-account ratelimit
```

Example exhausted output:

```
TYPE       ACTION        LIMIT    USED    REMAINING    RESET
token      write         100      0       100          N/A
token      read          1000     0       1000         N/A
account    read_write    1000     1000    0            2 hours from now
```

The `account read_write` row is what the alerts watch. `RESET` tells you when the window rolls over.

## Immediate actions (in order)

### 1. Trip the kill switch to stop bleeding

The kill switch pauses every `op` caller in our fleet (ansible wrappers, ESO via the token, the quota collector itself). This prevents further consumption while you investigate.

```bash
ssh command-center1 'sudo touch /var/lib/ansible-quasarlab/1p-killswitch'
```

Verify:

```bash
ssh command-center1 'ls -la /var/lib/ansible-quasarlab/1p-killswitch'
```

The next op-quota-collector firing will see `success=0` with reason `killswitch` and stop calling `op` until the file is removed.

### 2. Scale ESO to zero if the cap is exhausted or critical

ESO retries silently on 429 with a ~6-minute backoff, which burns 10 requests/hour per ExternalSecret. With ~10 ExternalSecrets, that is ~100 reqs/hr of pure retry churn that prevents the window from draining cleanly. Stop it entirely:

```bash
kubectl scale deploy external-secrets -n external-secrets --replicas=0
kubectl scale deploy external-secrets-webhook -n external-secrets --replicas=0
kubectl scale deploy external-secrets-cert-controller -n external-secrets --replicas=0
```

Leave ESO down until the `RESET` window in step 1 passes AND `remaining` climbs back to 1000. Then scale back to 1 replica each.

### 3. Identify the top consumer

The collector itself costs nothing: `op-quota-collector.timer` fires every 5 minutes (`OnCalendar=*:0/5`, 288 runs/day), and the only call it makes, `op service-account ratelimit`, does not count against the quota (see Confirm current state above). ESO contributes the largest variable share. Interactive `op` testing during an outage adds a spike. If this fires outside of a post-incident period, something changed.

Look at Grafana **1Password Quota** dashboard, "24h account usage" panel for the rate-of-change slope. Correlate spikes against:

- **ESO refresh interval**: should be `24h` on all 4 ExternalSecrets. Check [infrastructure/external-secrets/](/home/ladino/code/k8s-argocd/infrastructure/external-secrets/); see the refreshInterval history in PR #131.
- **Ansible playbook runs**: each one pulls 1-4 secrets via `op` wrappers. Frequent reruns during a debugging session are the #1 non-automation cause.
- **New service accounts**: when you add a new SA, ESO may kick off full sync of all referenced items, consuming N requests immediately.

### 4. After reset: reintroduce load gradually

1. Wait for `RESET` window to elapse. Re-probe with the single `op` call above.
2. Confirm `remaining` is back at 1000.
3. Remove kill switch: `ssh command-center1 'sudo rm /var/lib/ansible-quasarlab/1p-killswitch'`.
4. Scale ESO back up: `kubectl scale deploy external-secrets -n external-secrets --replicas=1` (and the two other deployments).
5. Watch the 1Password Quota dashboard for the first hour. Usage should rise slowly, not in a vertical spike.

## Root-cause patterns

| Symptom | Likely cause | Prevention |
|---|---|---|
| `Exhausted` fires once a day, same hour | ESO retry loop on a bad ExternalSecret reference | Fix the broken reference, don't blame the cap |
| `Exhausted` fires out of the blue | Interactive `op` testing without caching | Use the wrappers in [scripts/lib/op-secret-cache.sh](https://github.com/mithr4ndir/ansible-quasarlab/blob/main/scripts/lib/op-secret-cache.sh), not raw `op` loops |
| `CollectorStale` alone (no quota alert) | Collector script itself failed. Token unreadable, `op` missing, kill switch tripped but not cleared | `journalctl -t op-quota-collector -n 50` |
| `QuotaLow` but `reset` says 23 hours | Someone just started the window with a burst. Will resolve as the window slides or when they stop | Watch for 1 hour. If slope stays flat/decreasing, ignore |

## Burn Rate Alert

Applies to: `OnePasswordQuotaBurnRateHigh` (warning, more than 50 account reads in the trailing hour).

This fires on the speed of consumption, not on what is left, so it catches a burst minutes after it starts instead of hours later when `remaining` finally drops. Normal baseline is under 15 reads/hour. On 2026-09-12 three bursts (+160 at 04:18, +160 at 05:18, +94 at 14:33 UTC) were 74% of the day's usage and none of them paged.

### How the rule counts reads

The quota gauge drops once a day when the window rolls (for example 831 to 45), and the account counter restarts from zero. The rule scores every 1m step of `onepassword_ratelimit_used` over the trailing hour:

- Ordinary step: the climb since the previous minute. A decrease that is not a rollover scores 0.
- Rollover step: the whole post-reset value, because every read it shows happened after the reset. A rollover is recognised by `onepassword_ratelimit_reset_seconds` jumping up by more than an hour. Between rolls it only counts down; at both rolls in the 48h before this change it went 240 to 82800.

The first version of the rule (#200) clamped the rollover step to 0, which silently discarded every read between the reset and the first post-reset sample (#201). On 2026-09-13 that was 125 account reads in one sample, during a burst the collector's own token counter confirms (hourly token reads 4 to 254 between 02:46 and 02:51 UTC). The fixed rule pages on it.

Reads made after the last pre-reset sample but before the reset itself are still invisible, because the counter they landed in is gone by the next sample. That gap is at most one collector interval.

Do not "simplify" the rule to `increase()`: that treats any drop, not just a rollover, as a counter reset, and it has no way to tell a collector glitch from a real roll.

### Detection latency

The rule cannot see a read until the collector has sampled it. Worst case, for a burst big enough to cross 50 reads inside one collector interval, with the burst starting just after a collector run:

| Stage | Worst case | Source |
|---|---|---|
| Wait for the next collector run | 300s | `op-quota-collector.timer`, `OnCalendar=*:0/5` |
| Timer slack | 30s | `AccuracySec=30s` |
| node-exporter textfile scrape | 30s | `vm-node-exporter` `scrape_interval: 30s` |
| Subquery step alignment | 60s | `[1h:1m]` steps sit on whole minutes |
| Rule evaluation | 30s | `evaluation_interval: 30s`, `for: 0m` |
| Alertmanager grouping | 30s | default route `group_wait: 30s` |
| **Total to Discord** | **480s, about 8 minutes** | |

Typical latency is about half of each variable stage plus the fixed 30s grouping, about 4 minutes (150 + 15 + 15 + 30 + 15 + 30 = 255s). A slower consumer that crosses 50 reads over the hour is only caught when the hourly sum crosses 50, which can take up to the full hour. At the 2026-09-12 burst rate (+160 reads in one 5 minute interval, about 32 reads/min) an 8 minute worst case is about 256 reads, a quarter of the daily cap, before anyone is paged.

### Collector cadence

The 5 minute collector interval is the dominant term above. Moving `op-quota-collector.timer` to every minute (`OnCalendar=*:*:00`, `AccuracySec=5s`) cuts the worst case to 60 + 5 + 30 + 60 + 30 + 30 = 215s, and shrinks the pre-reset blind spot from 5 minutes to 1. The cost is not quota, since the ratelimit call is free: it is 1440 instead of 288 short `op` runs a day on command-center1, with their journal lines shipped by vector. Prometheus sample volume does not change, because node-exporter is scraped every 30s whether or not the textfile changed. The timer is defined in ansible-quasarlab (`roles/op_quota_collector`), so the change belongs there, and it should be watched for `onepassword_ratelimit_collector_success` dropping in case 1Password throttles the control-plane endpoint at that rate.

### Triage

1. Open the Grafana **1Password Quota** dashboard, "Account Burn Rate" panel, and note when the climb started.
2. On command-center1: `journalctl -t op-wrapper --since -1h` to find the caller.
3. Usual suspects: an ansible run that resolved the dynamic Proxmox inventory, a playbook rerun loop, or an ESO retry loop (check `ExternalSecretSyncErrorBurst`).
4. If the burst is ongoing and the cause is not obvious, trip the kill switch (step 1 above) before the daily cap drains.

## ExternalSecret Retry Alerts

Applies to: `ExternalSecretNotReady`, `ExternalSecretSyncErrorBurst`.

These fire **upstream** of the quota alerts above. They catch the retry loop at its source (a broken ExternalSecret reference) before it drains enough quota to trip `OnePasswordQuotaLow`. If one of these fires, the account cap is probably still healthy but an ES is actively consuming it.

### Triage

```bash
# See which ES is failing and the upstream error message
kubectl describe externalsecret -n "$NS" "$NAME"

# Check the ESO controller logs for the specific reconcile error
kubectl logs -n external-secrets deploy/external-secrets --tail=100 | grep -i "$NAME"

# Confirm the referenced 1Password item / field still exists
OP_SERVICE_ACCOUNT_TOKEN="$(cat ~/.config/op/service-account-token)" \
  op item get "<item-id>" --vault "<vault>" 2>&1 | head
```

### Common root causes

| Symptom | Likely cause | Fix |
|---|---|---|
| `item not found` in ESO logs | Referenced 1P item was deleted or moved vaults | Update the `remoteRef.key` in the ES spec, or recreate the item |
| `field not found` | Field renamed in the 1P item | Update `remoteRef.property` to match the current field name |
| `unauthorized` / `401` | Service account lost access to the vault | Re-grant vault access to the SA in 1P admin, or rotate the token |
| `429 Too Many Requests` in logs, condition flaps | 1P cap is already exhausted AND ESO is looping | Trip the kill switch (above), scale ESO to 0, wait for reset |

### Immediate containment if you can't fix the reference right now

Stop the retry loop by disabling the failing ES until you can fix it:

```bash
# Annotate to prevent reconcile (ESO respects this)
kubectl annotate externalsecret -n "$NS" "$NAME" \
  external-secrets.io/reconcile-paused="true" --overwrite

# Or delete the target Secret so at least the downstream workload fails loudly
kubectl delete secret -n "$NS" "$TARGET_SECRET_NAME"
```

Un-pause with `kubectl annotate ... external-secrets.io/reconcile-paused-` once fixed.

## Related

- RCA: `k8s-argocd/2026-04-18_etcd_instability_rca.md` (memory bank) includes the parallel 1P incident.
- Daily cap finding: `k8s-argocd/1password-daily-rate-limit.md` (memory bank).
- Collector role: [ansible-quasarlab/roles/op_quota_collector](https://github.com/mithr4ndir/ansible-quasarlab/tree/main/roles/op_quota_collector).
- Kill-switch library: [scripts/lib/op-killswitch.sh](https://github.com/mithr4ndir/ansible-quasarlab/blob/main/scripts/lib/op-killswitch.sh).
- Defense-in-depth PRs: #104 (kill switch), #105/#106 (secret caching), #131 (ESO 24h refresh).
