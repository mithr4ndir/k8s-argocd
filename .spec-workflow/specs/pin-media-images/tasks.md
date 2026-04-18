# Tasks: Pin All Media Container Images

- [x] 1. Get running versions from live cluster
  - Query each pod's logs for "Linuxserver.io version:" or container image version
  - Cross-reference with Docker Hub to confirm tags exist
  - _Requirements: 1, 2_

- [x] 2. Pin sonarr image to 4.0.17.2952-ls308
  - File: apps/media/sonarr/values.yaml
  - Change tag from `latest` to `4.0.17.2952-ls308`, pullPolicy to `IfNotPresent`
  - Run `make all` to re-render
  - _Requirements: 1_

- [x] 3. Pin radarr image to 6.1.1.10360-ls299
  - File: apps/media/radarr/values.yaml
  - Change tag from `latest` to `6.1.1.10360-ls299`, pullPolicy to `IfNotPresent`
  - Run `make all` to re-render
  - _Requirements: 1_

- [x] 4. Pin prowlarr image to 2.3.5.5327-ls142
  - File: apps/media/prowlarr/values.yaml
  - Change tag from `latest` to `2.3.5.5327-ls142`, pullPolicy to `IfNotPresent`
  - Run `make all` to re-render
  - _Requirements: 1_

- [x] 5. Pin bazarr image to v1.5.6-ls344
  - File: apps/media/bazarr/values.yaml
  - Change tag from `latest` to `v1.5.6-ls344`, pullPolicy to `IfNotPresent`
  - Run `make all` to re-render
  - _Requirements: 1_

- [x] 6. Pin jellyseerr image to 2.7.3
  - File: apps/media/jellyseer/values.yaml
  - Change tag from `latest` to `2.7.3`, pullPolicy to `IfNotPresent`
  - Run `make all` to re-render
  - _Requirements: 1_

- [x] 7. Pin qbittorrent image to 5.1.4-r3-ls450
  - File: apps/media/qbittorrent/values.yaml AND common.yaml (no makefile)
  - Change tag from `latest` to `5.1.4-r3-ls450`, pullPolicy to `IfNotPresent`
  - _Requirements: 1_

- [ ] 8. Merge PR #137 and verify ArgoCD sync
  - Confirm all pods restart with pinned versions
  - Verify no CrashLoopBackOff or image pull errors
  - _Requirements: 1, 2_
