# Requirements: Pin All Media Container Images

## Introduction

Pin all 6 remaining unpinned media app container images from `latest` to their currently running versions. This prevents silent breaking changes from upstream image updates, like the NZBGet v26 outage that paused all downloads.

## Requirements

### Requirement 1: Image Tag Pinning

**User Story:** As a homelab operator, I want all media container images pinned to specific versions, so that upstream breaking changes cannot silently break my services.

#### Acceptance Criteria

1. WHEN a media app pod restarts THEN the same image version SHALL be pulled regardless of which node schedules it
2. IF a new version is desired THEN the operator SHALL explicitly update the tag in values.yaml and re-render
3. WHEN `pullPolicy` is set THEN it SHALL be `IfNotPresent` for all pinned tags (no unnecessary re-pulls)

### Requirement 2: Cross-Node Consistency

**User Story:** As a homelab operator, I want identical container versions across all K8s nodes, so that pod rescheduling does not change application behavior.

#### Acceptance Criteria

1. WHEN a pod moves from k8cluster1 to k8cluster2 THEN the same application version SHALL run
2. IF `imagePullPolicy: Always` was previously set THEN it SHALL be changed to `IfNotPresent`

## Non-Functional Requirements

### Scope
- sonarr, radarr, prowlarr, bazarr, jellyseerr, qbittorrent
- jellyfin (already pinned to 10.10.6) and nzbget (pinned to v25.3-ls212 in PR #133) are excluded

### Rendering
- All apps except qbittorrent use `make all` to re-render Helm templates
- qbittorrent has no makefile (manually managed manifests)

## References
- GitHub Issue: #136
- PR: #137
- Incident: NZBGet v26 outage caused by unpinned `latest` tag
