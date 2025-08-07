# 🚀 k8s-argocd GitOps Platform – QuasarLab

This repository contains GitOps-ready configurations for deploying and managing services in a **Proxmox-hosted Kubernetes cluster**, using [ArgoCD](https://argo-cd.readthedocs.io/) for continuous delivery.

It includes:

- ✅ Workload management (e.g. Jellyfin) via Kustomize
- ✅ Infrastructure components like MetalLB
- ✅ Multi-environment support (`dev`, `prod`)
- ✅ Declarative, Git-backed deployment flows

---

## 📁 Repository Structure

```
k8s-argocd/
├── apps/
│   └── jellyfin/                  # Jellyfin app manifests
│       ├── deployment.yaml
│       ├── kustomization.yaml
│       ├── pv.yaml
│       ├── pvc.yaml
│       └── service.yaml
├── environments/
│   ├── dev/                       # Dev environment overlays
│   │   └── kustomization.yaml
│   └── prod/                      # Prod environment overlays
│       └── kustomization.yaml
├── infrastructure/
│   └── metallb/                   # MetalLB setup
│       ├── ipaddresspool.yaml
│       ├── kustomization.yaml
│       └── l2advertisement.yaml
└── README.md
```

---

## 🧱 Prerequisites

This repo assumes the following dependencies are **already deployed**:

| Component      | Description |
|----------------|-------------|
| Kubernetes     | v1.24+ cluster (Proxmox-hosted) |
| [MetalLB](https://metallb.universe.tf/) | LoadBalancer IP management |
| [NGINX Ingress](https://kubernetes.github.io/ingress-nginx/) | Handles routing to services |
| [ArgoCD](https://argo-cd.readthedocs.io/) | GitOps CD engine |
| Kustomize      | Used for overlays (v5.x recommended) |
| kubectl        | Cluster interaction |
| Git            | Source of truth |

---

## 🚦 How to Use This Repo

### Option A: Manual ArgoCD App Creation

Create the ArgoCD application that points to your `environments/dev` or `prod` directory:

```bash
argocd app create jellyfin-dev \
  --repo https://github.com/your-org/k8s-argocd.git \
  --path environments/dev \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace jellyfin \
  --sync-policy automated
```

### Option B: App-of-Apps (recommended)

You can define a parent app (like `bootstrap.yaml`) to manage all applications and environments in one place.

---

## 📦 Component Breakdown

### 🧩 Jellyfin App

Path: `apps/jellyfin/`

- Uses a Persistent Volume (PV) + PVC
- Reads from an external NFS mount (NAS)
- Exposed via a Kubernetes `Service`

Recommended IP-based access control on NFS to allow only your MetalLB-assigned IPs.

### 🌐 MetalLB

Path: `infrastructure/metallb/`

- `ipaddresspool.yaml`: Defines usable IPs
- `l2advertisement.yaml`: Enables broadcasting via L2
- `kustomization.yaml`: Bundles the manifests

Ensure you apply this config once MetalLB is installed.

```bash
kubectl apply -k infrastructure/metallb/
```

---

## 🌍 Environments

Environment overlays are located under:

- `environments/dev/`
- `environments/prod/`

These folders can:
- Reference `apps/*` and `infrastructure/*` modules
- Be used to scope ArgoCD apps per environment
- Include secrets or cluster-specific patches

---

## 🔁 Sync and Update Flow

All cluster changes are triggered by Git commits:

1. Edit app or infra manifest
2. Push to Git
3. ArgoCD detects changes and syncs them to your cluster

Check sync status:

```bash
argocd app list
argocd app get jellyfin-dev
```

---

## 🛠 Troubleshooting

```bash
kubectl get pods -A
kubectl describe svc jellyfin
kubectl logs -n argocd deploy/argocd-server
argocd app get jellyfin-dev
```

---

## 🧠 Lessons Learned: MetalLB on Control-Plane Nodes

During the deployment of MetalLB in a cluster where control-plane nodes are also used for workloads, a few important behaviors and gotchas surfaced that are worth documenting for future clusters.

---

### 🏷️ Problem: IPs were being bound to `kube-ipvs0`

MetalLB assigned external IPs, but they were being bound to the `kube-ipvs0` interface instead of a physical NIC like `eth0`. Despite that, the external IPs now **work correctly** after resolving underlying node eligibility issues.

#### 🔍 Root Causes
- MetalLB uses a default interface discovery logic unless `interfaces:` is set or exclusions are configured.
- All control-plane nodes were labeled with:
  ```
  node.kubernetes.io/exclude-from-external-load-balancers
  ```
  which caused MetalLB to **ignore those nodes entirely** for external IP announcement — regardless of the interface.

---

### ✅ Fixes Applied

#### 1. Removed the exclusion label

```bash
kubectl label node <node-name> node.kubernetes.io/exclude-from-external-load-balancers-
```

This allowed MetalLB to reassess control-plane nodes as valid candidates for announcing external IPs.

#### 2. Restarted MetalLB speakers

```bash
kubectl rollout restart daemonset <speaker-name> -n metallb-system
```

After restarting, the speakers re-announced the IPs — and **despite still binding them to `kube-ipvs0`**, the IPs now respond correctly to ARP and traffic from external clients.

---

### 🧪 How to Detect the Problem

- IP is assigned, but ping/curl from outside fails
- `ip addr show` shows the IP is bound to `kube-ipvs0`
- Node labels show: `exclude-from-external-load-balancers`

``` bash
# How to see if the label exists on the node
ladino@k8cluster1:~$ kubectl get nodes --show-labels | grep exclude-from-external
# Output that confirms the label exists
k8cluster1   Ready    control-plane   13d   v1.33.3   beta.kubernetes.io/arch=amd64,beta.kubernetes.io/os=linux,kubernetes.io/arch=amd64,kubernetes.io/hostname=k8cluster1,kubernetes.io/os=linux,node-role.kubernetes.io/control-plane=,node.kubernetes.io/exclude-from-external-load-balancers=
k8cluster2   Ready    control-plane   13d   v1.33.3   beta.kubernetes.io/arch=amd64,beta.kubernetes.io/os=linux,kubernetes.io/arch=amd64,kubernetes.io/hostname=k8cluster2,kubernetes.io/os=linux,node-role.kubernetes.io/control-plane=,node.kubernetes.io/exclude-from-external-load-balancers=
k8cluster3   Ready    control-plane   13d   v1.33.3   beta.kubernetes.io/arch=amd64,beta.kubernetes.io/os=linux,kubernetes.io/arch=amd64,kubernetes.io/hostname=k8cluster3,kubernetes.io/os=linux,node-role.kubernetes.io/control-plane=,node.kubernetes.io/exclude-from-external-load-balancers=
```
---

### 💡 Key Insight

Even if MetalLB binds the IP to `kube-ipvs0`, **it will work**, as long as:

- The node is eligible (label removed)
- Speaker pod is restarted (to reassess node state)
- No `interfaces:` constraint is set in `IPAddressPool`
- You're on a single-NIC or flat Layer 2 network

So while it may look incorrect at first glance, it's **functionally sound** under the right conditions.

---

### ✅ Recommendation for GitOps Clusters

For clusters that schedule workloads on control-plane nodes:

- It is safe to remove `exclude-from-external-load-balancers`
```bash
# How to remove
kubectl label node <nodename> node.kubernetes.io/exclude-from-external-load-balancers-
```
- No need to set `interfaces:` unless you must override defaults
- Use `excludeInterfaces.enabled: true` in `values.yaml` for safer defaults
- Document this behavior in your platform README

---

## 🚧 Roadmap

- [ ] Add TLS support via cert-manager
- [ ] Integrate Argo Rollouts
- [ ] Add Notification support (Slack, Discord)
- [ ] Add ApplicationSets for dynamic multi-cluster deployment

---

## 📚 Resources

- [ArgoCD Docs](https://argo-cd.readthedocs.io/)
- [MetalLB](https://metallb.universe.tf/)
- [Kustomize](https://kubectl.docs.kubernetes.io/references/kustomize/)
- [GitOps Best Practices](https://www.weave.works/technologies/gitops/)

---

> This repository is the source of truth for your infrastructure and application deployment. All changes should flow through Git.

