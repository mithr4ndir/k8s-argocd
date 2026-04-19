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

- [x] 6. Merge PR #138 and verify PostSync hook fires
  - PR #138 merged 2026-04-18. Endpoints object created at 192.168.1.170:8096.
  - Jellyseerr sync verified working against the in-cluster Service DNS.
  - _Requirements: 1_

- [x] 7. 2026-04-19 fix: replace broken bitnami/kubectl image (PR #143)
  - Issue: `bitnami/kubectl:1.31` went 404 when Bitnami removed public Docker Hub images in 2025. PostSync hook pods stuck in ImagePullBackOff for ~67 min. Endpoints object survived from prior runs so downstream Jellyseerr kept working, but self-healing was broken.
  - Fix: `alpine/kubectl:1.33.4` (matches cluster minor, has `/bin/sh` for heredoc, pinned to patch version).
  - Verified: manual `kubectl apply -f apps/media/jellyfin-endpoints-hook.yaml` ran Job to completion in 7s, log shows `endpoints/jellyfin configured`. ArgoCD will now run it cleanly on next sync.
  - Captured in memory bank (this repo) as supply-chain-drift learning: public base images can disappear, prefer pinned-patch + trivy scanning.
  - _Requirements: 1, 3_
