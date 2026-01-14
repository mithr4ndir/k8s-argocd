# AI Agent Instructions for k8s-argocd

## Project Overview
This is a GitOps-driven Kubernetes configuration repository for managing services in a Proxmox-hosted cluster using ArgoCD. The repository follows a structured approach to managing both infrastructure components and application workloads.

## Key Architecture Patterns

### Directory Structure
- `/apps/*` - Application-specific Kubernetes manifests, each with its own directory (e.g., `apps/jellyfin/`)
- `/environments/{dev,prod}` - Environment-specific Kustomize overlays
- `/infrastructure/*` - Core platform components like MetalLB

### Workload Management Pattern
1. Base configurations live in `/apps/{service-name}/`
2. Environment overlays in `/environments/{env}/` patch these bases
3. Each app directory should contain:
   ```
   kustomization.yaml   # Main resource manifest
   deployment.yaml      # Core workload definition
   service.yaml        # Network exposure
   pv.yaml            # Storage (if needed)
   pvc.yaml           # Storage claims
   ```

### Kustomize Conventions
- Use `kustomization.yaml` at every level
- Namespace definitions belong in base app directories
- Resources should be referenced relatively: `../../apps/service-name`

## Development Workflow

### Adding New Applications
1. Create new directory under `/apps/{new-service}/`
2. Define base resources (deployment, service, etc.)
3. Add entry to appropriate environment overlay
4. Test locally: `kubectl kustomize environments/dev`

### Infrastructure Changes
- MetalLB configs in `infrastructure/metallb/`
- Update IP pools via `ipaddresspool.yaml`
- Apply changes: `kubectl apply -k infrastructure/metallb/`

## Common Operations

### Local Testing
```bash
# Validate kustomize output
kubectl kustomize environments/dev

# Apply to cluster (if needed)
kubectl apply -k environments/dev
```

### ArgoCD Integration
- Applications should point to environment directories
- Example: `environments/dev` for dev environment
- Use automated sync policies where possible

## External Dependencies
- Kubernetes v1.24+
- ArgoCD for deployment
- MetalLB for LoadBalancer services
- NGINX Ingress for routing
- NFS storage for persistent volumes

## Best Practices
1. Always use Kustomize for environment-specific changes
2. Keep sensitive data out of Git - use Kubernetes secrets
3. Document IP ranges and service endpoints in component READMEs
4. Use consistent label schemas across all resources