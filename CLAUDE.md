# Claude Code Instructions for k8s-argocd

## Repository Purpose

GitOps-driven Kubernetes configuration repository for **QuasarLab**, a home lab running on Proxmox. Manages infrastructure and media applications via ArgoCD with automated sync.

## Critical: Helm + Kustomize Rendering Workflow

This repo does **NOT** deploy Helm releases directly. Instead:

1. **Edit `values.yaml`** in the app/component directory (this is the source of truth)
2. **Run `make all`** in that directory to render Helm templates into flat YAML manifests
3. **Commit the rendered output** (the flat YAMLs + updated kustomization.yaml)
4. **Push to main** — ArgoCD auto-syncs via Kustomize

**NEVER edit rendered manifests directly** (deployment.yaml, service.yaml, common.yaml, etc.) — they get overwritten by `make all`. Always edit `values.yaml` then re-render.

### Make targets available in most app directories:
- `make all` — render + update-kustomize (+ generate-crs for infra)
- `make render` — helm template with values.yaml, flatten output
- `make update-kustomize` — rebuild kustomization.yaml with kind-aware ordering using yq
- `make clean` — remove rendered files

### Required tools for rendering:
- `helm`, `kubectl`, `yq` (v4+)

## ArgoCD Configuration

- **ApplicationSet** `environments-applicationset` auto-discovers directories under `environments/*`
- Each environment directory becomes an ArgoCD Application (e.g., `environments/dev` -> app `dev`)
- Sync policy: `automated`, `prune: true`, `selfHeal: true`, `CreateNamespace=true`
- Repo: `https://github.com/mithr4ndir/k8s-argocd.git`, branch: `main`
- Monitoring apps (kube-prometheus-stack, filebeat) are deployed as **actual Helm releases** via separate ArgoCD Application resources with `helm:` source type

## Directory Structure

```
k8s-argocd/
├── environments/
│   ├── dev/kustomization.yaml      # References infrastructure/* and apps/*
│   └── prod/kustomizatiom.yaml     # (note: typo in filename, currently empty)
├── apps/
│   └── media/                      # Namespace: media
│       ├── kustomization.yaml      # References all media apps
│       ├── media-namespace.yaml
│       ├── media-pv-nfs.yaml       # NFS PV for shared media (192.168.1.15:/mnt/tank/media)
│       ├── jellyfin/               # Jellyfin media server (own Helm chart)
│       ├── sonarr/                 # bjw-s/app-template v3.5.1
│       ├── radarr/                 # bjw-s/app-template v3.5.1
│       ├── prowlarr/               # bjw-s/app-template v3.5.1
│       ├── bazarr/                 # bjw-s/app-template v3.5.1
│       ├── jellyseer/              # bjw-s/app-template v3.5.1
│       ├── nzbget/                 # k8s-at-home chart v12.4.2
│       └── qbittorrent/            # No makefile (manually managed)
├── infrastructure/
│   ├── metallb/                    # MetalLB v0.15.2 (IP pool: 192.168.1.225-240)
│   ├── nfs/                        # NFS subdir external provisioner v4.0.18
│   │                               # StorageClass: k8s-nfs, NFS: 192.168.1.15:/mnt/tank/k8s
│   ├── nvidia/                     # RuntimeClass "nvidia" (device-plugin disabled)
│   ├── reloader/                   # Stakater Reloader v1.4.14 (auto-restart on ConfigMap/Secret changes)
│   ├── external-secrets/           # External Secrets Operator + ClusterSecretStore (1Password)
│   └── monitoring/                 # kube-prometheus-stack v65.8.1, filebeat v8.5.1
│       ├── discord-alert-proxy/    # Themed alert proxy (Alertmanager → Discord embeds)
│       ├── application.yaml        # ArgoCD Application resources (Helm type)
│       ├── kube-prometheus-stack/   # Helm chart wrapper (Chart.yaml + values.yaml)
│       └── filebeat/               # Helm chart wrapper (Chart.yaml + values.yaml)
```

## Helm Chart Sources by App

| App | Chart | Version | Rendering |
|-----|-------|---------|-----------|
| jellyfin | jellyfin/jellyfin | 2.3.0 | `make all` via Helm repo |
| sonarr, radarr, prowlarr, bazarr, jellyseer | bjw-s/app-template | 3.5.1 | `make all` via OCI |
| nzbget | k8s-at-home | 12.4.2 | `make all` via Helm repo |
| qbittorrent | N/A | N/A | Manually managed (no makefile) |
| metallb | metallb/metallb | 0.15.2 | `make all` via Helm repo |
| nfs-provisioner | nfs-subdir-external-provisioner | 4.0.18 | `make all` via Helm repo |
| kube-prometheus-stack | prometheus-community | 65.8.1 | ArgoCD Helm release (not pre-rendered) |
| filebeat | elastic | 8.5.1 | ArgoCD Helm release (not pre-rendered) |

## Key Patterns

### bjw-s/app-template apps (sonarr, radarr, etc.)
- Render into a single `common.yaml` containing all resources (PVC, Service, Deployment)
- kustomization.yaml just references `- common.yaml`
- All share the `jellyfin-media` PVC for media access
- All use LoadBalancer with shared IP `192.168.1.226` via `metallb.universe.tf/allow-shared-ip: media-shared`

### Jellyfin (standalone Helm chart)
- Renders into separate files (deployment.yaml, service.yaml, etc.)
- Uses `nvidia` RuntimeClass for GPU access (RTX 2080 Ti NVENC transcoding)
- nodeSelector: `nvidia.com/gpu: "true"` pins to k8cluster2
- Kustomize patches extend Helm output (since chart doesn't support initContainers/lifecycle):
  - `patch-encoding-init.yaml` — initContainer copies encoding.xml from ConfigMap to PVC
  - `patch-graceful-shutdown.yaml` — preStop hook kills ffmpeg, 120s termination grace
- When adding Kustomize patches, use `make render` (not `make all`) to avoid overwriting kustomization.yaml
- `encoding-configmap.yaml` is manually managed (not Helm-generated), survives `make render`

#### Jellyfin Operational Warnings
- **NEVER restart/redeploy Jellyfin while a stream is active** — killing ffmpeg with open CUDA contexts causes Xid 45 GPU faults, requiring a full VM reboot to recover
- **Config on NFS is vulnerable to corruption** during pod restarts (Recreate strategy + NFS write truncation). SQLite databases (users.db, library.db) cannot be in git — protected by ZFS snapshots on TrueNAS
- **encoding.xml must be writable** — Jellyfin writes to it at startup. ConfigMap subPath mounts are read-only at filesystem level even without readOnly flag. Use initContainer copy pattern instead
- **4K HEVC DV HDR transcoding** can cause CUDA_ERROR_OUT_OF_MEMORY on RTX 2080 Ti (11GB). Mitigations: disable enhanced NVDEC decoder, enable throttling, or lower client bitrate
- **Client streaming bitrate** set too high (or "Auto") causes Jellyfin to remux (copy) instead of transcode, sending raw 40Mbps+ 4K over WAN which causes skipping. Set to 20Mbps for WAN clients

#### *Arr Apps Operational Warning (Sonarr, Radarr, Bazarr, Prowlarr, etc.)
- **NEVER use `kubectl rollout restart`** on *arr apps — their SQLite config databases are on NFS PVCs. Even with `Recreate` strategy, rollout restart creates a new ReplicaSet that can briefly overlap, and NFS doesn't release file locks cleanly. This caused a full DB corruption (zeroed SQLite header) on 2026-03-15.
- **To restart *arr pods safely**, scale down then up with a pause to let NFS flush:
  ```bash
  kubectl scale deploy <name> -n media --replicas=0 && sleep 5 && kubectl scale deploy <name> -n media --replicas=1
  ```
- *Arr apps have weekly automatic backups at `/config/Backups/scheduled/`. If corruption occurs, restore with:
  ```bash
  kubectl exec -n media deploy/<name> -- unzip -o /config/Backups/scheduled/<latest>.zip <name>.db -d /config/
  ```

### Infrastructure components
- MetalLB and NFS provisioner have `generate-crs` targets that create namespace + custom resource YAMLs
- NFS provisioner makefile also has `inject-namespace` target

## Networking

- All media services share LoadBalancer IP `192.168.1.226` via MetalLB annotation
- MetalLB IP pool: `192.168.1.225-192.168.1.240` (L2 mode)
- NFS server: `192.168.1.15` (TrueNAS SCALE)
- NFS mount options: `nfsvers=4.1, hard, timeo=600, retrans=2`

## Secrets

- Managed via External Secrets Operator (ESO) pulling from 1Password
- ClusterSecretStore `onepassword-infrastructure` references the `Infrastructure` vault
- ESO remoteRef format: `<item-id>/<field>` (NOT `op://` URI)
- Excluded from git via `.gitignore` (`**/secret.yaml`, `**/*-secret.yaml`)

## Stakater Reloader

- Auto-restarts pods when their mounted ConfigMaps or referenced Secrets change
- Add `reloader.stakater.com/auto: "true"` annotation to pod template metadata
- Currently annotated: discord-alert-proxy, jellyfin (via Kustomize patch)
- For Helm-rendered deployments, add the annotation via a Kustomize patch file (not directly in the rendered YAML)

## Discord Alert Proxy

- FastAPI proxy at `infrastructure/monitoring/discord-alert-proxy/`
- Alertmanager → webhook_configs → proxy pod (port 9095) → Discord API
- Themed embeds: LOTR/Balrog (critical/red), Star Wars (warning/orange), fantasy (resolved/green)
- Groups alerts into single embed, edits existing messages in place on repeat/resolve
- Discord webhook URL from 1Password via ExternalSecret

## Environment Notes

- `environments/prod/kustomizatiom.yaml` has a typo in filename and is empty
- Only `environments/dev` is actively used
