# Tasks: ArgoCD PostSync Hook for Jellyfin Endpoints

- [x] 1. Evaluate approaches for working around ArgoCD Endpoints exclusion
  - Considered: env vars in Jellyseerr (not viable, settings.json on PVC), ExternalName (IPs only), removing global exclusion (performance impact), CronJob (unnecessary complexity)
  - Selected: ArgoCD PostSync hook Job
  - _Requirements: 1, 3_

- [x] 2. Create PostSync hook manifest
  - File: apps/media/jellyfin-endpoints-hook.yaml
  - ServiceAccount, Role (endpoints: get/create/update/patch), RoleBinding, Job
  - Job uses `kubectl apply` with inline Endpoints manifest targeting 192.168.1.170:8096
  - Annotations: `argocd.argoproj.io/hook: PostSync`, `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation,HookSucceeded`
  - _Requirements: 1, 2_

- [x] 3. Harden the Job container
  - Non-root (uid 65534), read-only rootfs, all capabilities dropped
  - Seccomp RuntimeDefault profile, resource limits (10m/32Mi request, 100m/64Mi limit)
  - _Requirements: 2_

- [x] 4. Update kustomization.yaml
  - File: apps/media/kustomization.yaml
  - Replace `jellyfin-external-ep.yaml` reference with `jellyfin-endpoints-hook.yaml`
  - Keep `jellyfin-external-ep.yaml` in repo as reference documentation
  - _Requirements: 3_

- [x] 5. Validate Kustomize rendering
  - `kubectl kustomize apps/media/` builds cleanly
  - `kubectl kustomize environments/dev/` builds cleanly
  - All hook resources receive correct `media` namespace
  - _Requirements: 3_

- [ ] 6. Merge PR #138 and verify PostSync hook fires
  - Confirm Job runs after ArgoCD sync
  - Verify Endpoints object exists: `kubectl get endpoints jellyfin -n media`
  - Verify Jellyseerr sync: check logs for successful Jellyfin API calls
  - _Requirements: 1_
