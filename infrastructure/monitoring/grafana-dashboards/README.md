# Grafana dashboards

**These ConfigMaps are the single source of truth for Grafana dashboards.**

Grafana is not a standalone app in this repo. It is a subchart of
`kube-prometheus-stack` (`grafana.enabled: true` in
`../kube-prometheus-stack/values.yaml`), deployed by the ArgoCD application of
the same name.

## How a dashboard reaches Grafana

The chart's Grafana sidecar watches for ConfigMaps and loads them:

```yaml
grafana:
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard      # ConfigMap must carry grafana_dashboard: "1"
      folderAnnotation: grafana_folder
      provider:
        allowUiUpdates: true
```

Any ConfigMap in the `monitoring` namespace labelled `grafana_dashboard: "1"`
is picked up and filed under the folder in its `grafana_folder` annotation. No
restart needed.

## UI edits persist, and will silently outrank this repo

This is the opposite of what you might assume from `persistence.enabled: false`.
That setting only removes the pod's PVC. Grafana stores dashboards in **external
PostgreSQL** (`192.168.1.123:5432`, configured under `grafana.ini.database` in
the chart values), so a dashboard edited in the browser is written to that
database and **survives pod restarts**.

The sidecar provisioner runs with:

```yaml
allowUiUpdates: true
updateIntervalSeconds: 30
```

`allowUiUpdates: true` lets you edit a provisioned dashboard in the UI. Grafana's
file provisioner will then not overwrite it unless the file's `version` is
**higher** than the version stored in the database. Every dashboard here ships
`version: 1`, and saving in the UI bumps the stored copy past that.

So a UI edit becomes the live truth and this repo quietly stops being applied to
that dashboard. That is not hypothetical: it is a second mechanism for exactly
the drift described under History below.

## Committing a change back

```bash
# 1. In Grafana: Dashboard settings -> JSON Model -> copy, or Share -> Export.
#    The export carries the current stored version, which matters for step 2.
# 2. Convert. The version is incremented by default so the file outranks
#    whatever is in the database and provisioning actually applies it.
scripts/dashboard-to-configmap.py /tmp/jellyfin.json --folder VMs --name vms-jellyfin
```

Flags:

| Flag | Effect |
|---|---|
| *(default)* | `version` = input version + 1, so the file wins |
| `--version N` | set an explicit version, if a dashboard was edited repeatedly |
| `--keep-version` | leave `version` untouched |

Then commit the regenerated ConfigMap; ArgoCD syncs it and the sidecar reloads
within `updateIntervalSeconds`.

Do not hand-edit the JSON embedded in these files. The script parses the export
and rejects anything whose `panels` is not a list, so a malformed dashboard fails
locally rather than landing in the cluster as something Grafana will not render.

!!! note
    If you want this repo to be strictly authoritative and UI edits to be
    impossible, set `allowUiUpdates: false` in the chart values. That is a
    deliberate trade: it also stops people iterating on a dashboard in the
    browser, which is how most of these were built.

## History

Dashboards used to live as loose JSON in `observability-quasarlab` under
`grafana/dashboards/`, deployed by the `grafana_config` Ansible role back when
Grafana ran on a VM. Grafana moved to Kubernetes in #111 (2026-04-05) and that
role stopped being reachable: `grafana_config.yml` targets a `grafana` host
group that no longer exists in any inventory, so every hourly run logged
`skipping: no hosts matched`.

Nothing noticed, because the ConfigMaps kept working. The two copies then drifted
for months. The Jellyfin dashboard was the worst case: dead GPU panels were
removed from the JSON on 2026-05-31 when the card left that VM, but the
ConfigMap was never regenerated, so the live dashboard still rendered 20 GPU
panels against metrics nothing produced. Found on 2026-08-24 while investigating
a disk-exhaustion incident on that same host, where the dashboard was no help.

That is why there is now exactly one source of truth.
