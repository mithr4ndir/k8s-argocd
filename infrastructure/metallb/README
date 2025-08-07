# MetalLB (Layer 2) – GitOps Deployment

This folder contains the declarative manifests and automation logic for deploying [MetalLB](https://metallb.universe.tf/) in Layer 2 mode using Helm and Kustomize. All files are managed via `Makefile` automation and are designed for GitOps-friendly workflows (e.g., ArgoCD or Flux).

---

## 📦 Contents

| File                     | Description |
|--------------------------|-------------|
| `values.yaml`            | Helm chart values for MetalLB configuration |
| `controller.yaml`        | Rendered Deployment for the MetalLB controller |
| `speaker.yaml`           | Rendered DaemonSet for the MetalLB speaker |
| `rbac.yaml`              | RBAC resources rendered from the Helm chart |
| `service-accounts.yaml`  | Controller/speaker ServiceAccounts |
| `webhooks.yaml`          | Webhook-related resources |
| `crds.yaml`              | MetalLB CRDs |
| `metallb-namespace.yaml` | Namespace manifest (`metallb-system`) |
| `ipaddresspool.yaml`     | Custom Layer 2 IP pool definition |
| `l2advertisement.yaml`   | L2Advertisement to enable IP broadcasting |
| `kustomization.yaml`     | Auto-generated list of manifests for Kustomize |
| `Makefile`               | Declarative rendering, cleanup, and automation logic |

---

## 🛠 Usage

### ✅ Render All Resources

To render MetalLB manifests into the current directory and rebuild the `kustomization.yaml`:

```bash
make all
```

This will:
- Render Helm chart using `values.yaml`
- Flatten all `.yaml` resources into the current directory
- Generate:
  - `ipaddresspool.yaml`
  - `l2advertisement.yaml`
  - `metallb-namespace.yaml`
- Rebuild `kustomization.yaml` with kind-aware ordering

### 🧹 Clean Generated Files

To remove all rendered `.yaml` files except for `values.yaml` and `kustomization.yaml`:

```bash
make clean
```

---

## 📐 Best Practices

- Use `make all` after modifying `values.yaml`
- Do **not** hand-edit `kustomization.yaml` — it is dynamically generated
- Keep `values.yaml` minimal and Layer 2 focused unless BGP is required
- ArgoCD/Flux should point to this folder and track `kustomization.yaml`

---

## 🔄 Sync Order (Kubernetes Kind Priority)

`kustomization.yaml` is sorted based on resource dependencies:

1. `Namespace`
2. `CRDs`
3. `RBAC` (ServiceAccounts, Roles)
4. Core workloads (controller, speaker)
5. Webhooks
6. Custom CRs (e.g., IP pools, L2 advertisements)

---

## 📋 Requirements

- [yq v4+ (Go version)](https://github.com/mikefarah/yq)
- Helm v3
- Make
- Kustomize or ArgoCD

To check:

```bash
yq --version  # Should print v4+
helm version
make --version
```

---

## 🔗 References

- 📘 MetalLB: https://metallb.universe.tf/
- 📦 Helm Chart: https://github.com/metallb/metallb/tree/main/charts/metallb
