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

## UI edits do not persist

`grafana.persistence.enabled` is `false`, so Grafana has no PVC. `allowUiUpdates`
lets you edit in the browser, but **the pod restarting throws that away.** To
keep a change:

1. Edit in Grafana, then **Dashboard settings → JSON Model → copy**, or use
   Share → Export.
2. Save it somewhere temporary and convert it:

   ```bash
   scripts/dashboard-to-configmap.py /tmp/jellyfin.json --folder VMs --name vms-jellyfin
   ```

3. Commit the regenerated ConfigMap. ArgoCD syncs it and the sidecar reloads.

Do not hand-edit the JSON embedded in these files. The script parses the export
first, so a malformed dashboard fails locally rather than landing in the cluster
as something Grafana refuses to render.

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
