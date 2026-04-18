# Requirements: ArgoCD PostSync Hook for Jellyfin Endpoints

## Introduction

Jellyfin runs as a VM (192.168.1.170), not in K8s. A headless Service + manually-managed Endpoints object provides in-cluster DNS resolution (`jellyfin.media.svc.cluster.local`). However, ArgoCD's global `resource.exclusions` in `argocd-cm` excludes all Endpoints and EndpointSlice resources, so the Endpoints manifest in git is never synced.

## Requirements

### Requirement 1: Automated Endpoints Creation

**User Story:** As a homelab operator, I want the Jellyfin Endpoints object to be automatically created and maintained, so that Jellyseerr can reach Jellyfin via K8s DNS without manual intervention.

#### Acceptance Criteria

1. WHEN ArgoCD syncs the `dev` application THEN the Jellyfin Endpoints object SHALL exist in the `media` namespace
2. IF the Endpoints object is deleted THEN the next ArgoCD sync SHALL recreate it
3. WHEN the cluster is rebuilt from scratch THEN the Endpoints SHALL be created automatically

### Requirement 2: Security and Least Privilege

**User Story:** As a homelab operator, I want the automation to follow least-privilege principles, so that it cannot modify resources beyond what is needed.

#### Acceptance Criteria

1. WHEN the PostSync hook Job runs THEN it SHALL use a dedicated ServiceAccount with only `get/create/update/patch` on `endpoints` in the `media` namespace
2. IF the Job container runs THEN it SHALL run as non-root with read-only root filesystem and all capabilities dropped

### Requirement 3: IaC Compliance

**User Story:** As a homelab operator, I want this fully managed in git, so that it survives cluster rebuilds without manual steps.

#### Acceptance Criteria

1. WHEN the solution is deployed THEN all resources (SA, Role, RoleBinding, Job) SHALL be defined in git
2. IF ArgoCD self-heals THEN the hook resources SHALL persist between syncs

## References
- GitHub Issue: #134
- PR: #138
- Root cause: ArgoCD `argocd-cm` ConfigMap globally excludes Endpoints and EndpointSlice
