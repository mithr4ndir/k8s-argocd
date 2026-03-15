# k8s-argocd — QuasarLab GitOps Platform

GitOps-driven Kubernetes configuration for a Proxmox-hosted homelab. ArgoCD auto-syncs everything from this repo.

## How It Works

1. Edit `values.yaml` in the app/component directory
2. Run `make all` to render Helm templates into flat YAML manifests
3. Commit and push — ArgoCD auto-syncs via Kustomize

> **Never edit rendered manifests directly** — they get overwritten by `make all`.

## What's Deployed

### Media Apps (namespace: `media`)

All share a LoadBalancer IP (`192.168.1.226`) via MetalLB and an NFS PV from TrueNAS.

| App | Chart | Purpose |
|-----|-------|---------|
| Sonarr | bjw-s/app-template v3.5.1 | TV show management |
| Radarr | bjw-s/app-template v3.5.1 | Movie management |
| Prowlarr | bjw-s/app-template v3.5.1 | Indexer manager |
| Bazarr | bjw-s/app-template v3.5.1 | Subtitle management |
| Jellyseer | bjw-s/app-template v3.5.1 | Content requests |
| NZBGet | k8s-at-home v12.4.2 | Usenet downloader |
| qBittorrent | Manual manifests | Torrent client |

> **Jellyfin** was migrated off K8s to a dedicated VM with native GPU passthrough. See [ansible-quasarlab](https://github.com/mithr4ndir/ansible-quasarlab) for its configuration.

### Infrastructure (namespace: various)

| Component | Version | Purpose |
|-----------|---------|---------|
| MetalLB | 0.15.2 | L2 LoadBalancer (IP pool: `192.168.1.225-240`) |
| NFS Provisioner | 4.0.18 | Dynamic PVCs via TrueNAS (`192.168.1.15:/mnt/tank/k8s`) |
| External Secrets Operator | Latest | Fetches secrets from 1Password |
| Stakater Reloader | 1.4.14 | Auto-restarts pods on ConfigMap/Secret changes |

### Monitoring (namespace: `monitoring`)

Deployed as Helm releases via separate ArgoCD Applications (not pre-rendered).

| Component | Version | Purpose |
|-----------|---------|---------|
| kube-prometheus-stack | 65.8.1 | Prometheus + Alertmanager + Grafana |
| Loki | Latest | Log aggregation |
| Vector (DaemonSet) | 0.36.1 | K8s container log shipping to Loki |
| Vector Aggregator | 0.36.1 | External syslog/agent ingestion (`192.168.1.232`) |
| Discord Alert Proxy | Custom | LOTR/Star Wars themed alert embeds |

### Alert Rules

| Alert | Severity | Trigger |
|-------|----------|---------|
| PveQuorumDegraded | warning | Cluster votes < expected |
| PveQuorumLost | critical | Cluster lost quorum |
| PveGuestDown | warning | VM with autostart is down |
| PveHighCpu / PveHighMemory | warning | Node resource > 90% |
| NodeDown | critical | Proxmox node unreachable |
| ServiceDown / ServiceInactive | warning | Monitored systemd unit offline |
| RebootRequired | warning | Pending reboot after updates |
| AnsiblePlaybookFailed | warning | Playbook returned failure |

## Repository Structure

```
k8s-argocd/
├── apps/media/              # Media namespace apps (Sonarr, Radarr, etc.)
├── environments/
│   └── dev/                 # Active environment (references infra + apps)
├── infrastructure/
│   ├── metallb/             # L2 LoadBalancer
│   ├── nfs/                 # NFS storage provisioner
│   ├── nvidia/              # RuntimeClass (device-plugin disabled)
│   ├── reloader/            # ConfigMap/Secret change watcher
│   ├── external-secrets/    # ESO + 1Password ClusterSecretStore
│   └── monitoring/          # Prometheus stack, Loki, Vector, alert proxy
└── bootstrap/               # Initial cluster bootstrap manifests
```

## Prerequisites

| Tool | Purpose |
|------|---------|
| `helm` | Template rendering |
| `yq` v4+ | YAML processing |
| `kubectl` | Cluster interaction |
| `make` | Render workflows (`make all`, `make render`) |

## Related Repos

| Repository | Purpose |
|------------|---------|
| [ansible-quasarlab](https://github.com/mithr4ndir/ansible-quasarlab) | VM provisioning, Jellyfin, monitoring agents, Wazuh |
| [terraform-quasarlab](https://github.com/mithr4ndir/terraform-quasarlab) | Proxmox VM infrastructure |
| [observability-quasarlab](https://github.com/mithr4ndir/observability-quasarlab) | Grafana dashboards and provisioning |
