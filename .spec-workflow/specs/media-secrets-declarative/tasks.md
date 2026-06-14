# Tasks Document

Order: tasks 1-2 audit the current state, tasks 3-5 ship Jellyseerr as the prototype, tasks 6-8 extend to Sonarr/Radarr/Prowlarr/Bazarr/NZBGet/qBittorrent, task 9 generalizes, task 10 smoke-tests, task 11 closes the public-repo leak gate.

- [ ] 1. Inventory every sensitive field currently stored in a media-namespace config file
  - File: none (investigation). Output captured inline in task comment and mirrored into `docs/runbooks/media-secrets-inventory.md` draft.
  - Scope is broader than "API keys": cover every field matching the classification in `requirements.md` (account usernames/emails, Usenet server hostnames + ports + SSL flags, account passwords, API keys, indexer display names + URLs + keys, tracker cookies, RSS feed URLs with tokens, download client auth).
  - Apps in scope: Jellyseerr (`/app/config/settings.json`), Sonarr (`/config/config.xml`), Radarr (`/config/config.xml`), Prowlarr (`/config/config.xml` + indexer table in `prowlarr.db`), Bazarr (`/config/config/config.ini` + `config.yaml`), NZBGet (`/config/nzbget.conf`), qBittorrent (`/config/qBittorrent/qBittorrent.conf`), Jellyfin (only API keys/users).
  - For each field, note: app, file path, JSON/XML/INI path, current value (to be rotated), suggested 1P item name, classification (API key / account / provider hostname / indexer / tracker / other).
  - For Prowlarr specifically: dump the full indexer list via the API (`GET /api/v1/indexer`) and capture name+URL+key+categories per entry; this becomes the JSON blob for Requirement 3b.
  - Purpose: Ground-truth the expanded scope before writing any manifests.
  - _Leverage: `kubectl exec` + the Sonarr/Radarr/Prowlarr REST APIs for live state_
  - _Requirements: 1.1, 3b.1, 4.1_

- [ ] 2. Rotate every inventoried key in 1Password and create the 1P items
  - File: 1Password `Infrastructure` vault (out of repo), using write-capable token `~/.op_service_account_token`.
  - For every field from task 1, either reuse an existing 1P item (e.g. `jellyfin-api`) or create a new one. Rotate the value at creation time. Treat the old cleartext value as leaked.
  - Purpose: Stop the current secrets from being authoritative before the merge machinery exists.
  - _Leverage: `reference_1password_tokens.md` for the write-capable token location_
  - _Requirements: 1.1, 1.4_

- [ ] 3. Create the Jellyseerr ExternalSecret and merge-script ConfigMap
  - Files:
    - `apps/media/jellyseer/externalsecret.yaml` (new; pulls jellyseerr-api, jellyseerr-vapid, jellyseerr-client-id, jellyfin-api, sonarr-api, radarr-api into one Secret `jellyseerr-secrets`)
    - `apps/media/jellyseer/merge-script-configmap.yaml` (new; holds `merge.sh` which takes `/secrets/*` + `/config/settings.json` and writes merged settings.json)
    - `apps/media/jellyseer/kustomization.yaml` (update to include the two new files)
  - The merge script: bail out if `/config/settings.json` does not exist (first install); otherwise read each secret file, `jq --arg ... 'setpath([...])'` for each field, write atomically.
  - `spec.refreshInterval: 24h` on the ExternalSecret.
  - Purpose: The 1P → Secret pipeline and the logic that will use it.
  - _Leverage: existing `externalsecret.yaml` patterns in `apps/dashboard/homepage/`_
  - _Requirements: 1.2, 2.1, 2.4, 3.1_

- [ ] 4. Wire the initContainer into Jellyseerr values.yaml
  - File: `apps/media/jellyseer/values.yaml`
  - Add `controllers.main.initContainers.secrets-merge` with:
    - Image: `ghcr.io/alpine/alpine:3.20` (or an image with `jq` prebuilt; pin by digest per `pin-media-images` convention)
    - Command: `sh /scripts/merge.sh`
    - Mounts: `/config` from the existing PVC, `/secrets` from `jellyseerr-secrets`, `/scripts` from the merge-script ConfigMap
    - Security context: non-root when feasible; falls back to match main container UID so the merged file stays readable
    - Resource requests/limits appropriate for a 2-second task
  - Purpose: Actually run the merge before main container starts.
  - _Leverage: bjw-s `app-template` initContainers schema already used elsewhere if present, otherwise the raw pod-template spec_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 5. Cut-over and validate Jellyseerr
  - File: none (operational)
  - Before deploy: take a backup of the existing `/app/config/settings.json` from inside the pod.
  - Deploy. Exec into the pod. Confirm the merged settings.json has the new rotated values in the expected JSON paths and that every non-secret field (libraries, permissions, user records) is preserved byte-for-byte (diff against backup).
  - Smoke test: open a request in Jellyseerr, verify it propagates to Sonarr (proves sonarr apiKey works). Open a Jellyfin-login test (proves jellyfin apiKey works).
  - Purpose: Prove the Jellyseerr prototype works end-to-end before extending to other apps.
  - _Leverage: existing Jellyseerr → Sonarr integration test flow_
  - _Requirements: all Jellyseerr-related_

- [ ] 6. Extend to Sonarr (config.xml)
  - Files:
    - `apps/media/sonarr/externalsecret.yaml` (new)
    - `apps/media/sonarr/merge-script-configmap.yaml` (new; XML merge via `xmlstarlet ed -u '/Config/ApiKey' -v ...`)
    - `apps/media/sonarr/values.yaml` (add initContainer)
    - `apps/media/sonarr/kustomization.yaml` (update)
  - Purpose: Apply the same pattern to the XML-config case
  - _Leverage: task 3-4 as the template_
  - _Requirements: 5.2 (pattern reuse)_

- [ ] 7. Extend to Radarr
  - Files mirror task 6 under `apps/media/radarr/`
  - Purpose: Same
  - _Leverage: task 6_
  - _Requirements: 5.2_

- [ ] 8a. Extend to Prowlarr (API key merge + indexer blob sync)
  - Files mirror tasks 6/7 under `apps/media/prowlarr/` for the `config.xml` API key merge.
  - Additional file: `apps/media/prowlarr/indexer-sync-job.yaml` (new). A post-start Job (NOT an initContainer, since Prowlarr's API is needed) that reads the indexer JSON blob from `/secrets/indexers.json` and POSTs each entry to `http://localhost:8989/api/v1/indexer` with the Prowlarr API key. Runs on pod start via a lifecycle hook or a small sidecar.
  - The JSON blob itself is populated via task 1's Prowlarr inventory, sanitized for tokens, committed to 1P as a single item (field name `indexers_json`).
  - Purpose: Cover Requirement 3b. Prowlarr is the only app where the sensitive data is a list-of-objects, not a scalar field.
  - _Leverage: Prowlarr REST API, tasks 6/7 for the scalar fields_
  - _Requirements: 1.1, 3b.1, 3b.2, 3b.4_

- [ ] 8b. Extend to NZBGet (Usenet provider + control credentials)
  - Files under `apps/media/nzbget/`: ExternalSecret + merge-script ConfigMap + values.yaml initContainer.
  - INI-format merge (nzbget.conf is key=value). Merge: `Server1.Host`, `Server1.Port`, `Server1.Username`, `Server1.Password`, `Server1.Encryption`, plus `ControlUsername`, `ControlPassword`, `AddUsername`, `AddPassword`, `RestrictedUsername`, `RestrictedPassword`. Use `sed -i` or `crudini` for INI edits; script kept under 40 lines as per NFR.
  - Purpose: Keep every provider host/credential out of Git.
  - _Leverage: tasks 6/7 as the template_
  - _Requirements: 1.1, 5.2_

- [ ] 8c. Extend to qBittorrent (WebUI auth + optional tracker auth)
  - Files under `apps/media/qbittorrent/`: ExternalSecret + merge-script ConfigMap + values.yaml initContainer.
  - Merge the `WebUI\\Password_PBKDF2` line and `WebUI\\Username` in `qBittorrent.conf` plus any categories containing tracker cookies (likely in `BT_backup/categories.json` if present).
  - Purpose: Close the downloader-auth gap.
  - _Leverage: tasks 6/7_
  - _Requirements: 1.1, 5.2_

- [ ] 8d. Extend to Bazarr (provider API keys)
  - Files under `apps/media/bazarr/`: ExternalSecret + merge-script ConfigMap + values.yaml initContainer.
  - INI/YAML merge for `config.ini` / `config.yaml`: provider API keys (OpenSubtitles credentials, Subscene, etc.) plus the Bazarr own API key.
  - Purpose: Same-shape coverage for the subtitle side of the stack.
  - _Leverage: tasks 6/7_
  - _Requirements: 1.1, 5.2_

- [ ] 9. Generalize the pattern into a reusable Kustomize component
  - File: `infrastructure/components/secret-merge-initcontainer/` (new)
    - `kustomization.yaml` declaring it a component
    - `initcontainer-patch.yaml` template snippet
    - `README.md` documenting how to consume it (secret name + merge-script ConfigMap name as variables)
  - Refactor Jellyseerr / Sonarr / Radarr / Prowlarr to consume the component, reducing each app's diff to just the per-app ExternalSecret and merge-script ConfigMap.
  - Purpose: Reduce the per-app cost to "add the secret, add the merge script, consume the component".
  - _Leverage: Kustomize components (already used if present in the repo; otherwise introduce them here)_
  - _Requirements: 5.1, 5.2, 5.3_

- [ ] 10. Document, runbook, and smoke-test
  - Files:
    - `docs/runbooks/media-secrets-inventory.md` (finalize from task 1 draft, include the full field classification, not just API keys)
    - `docs/patterns/secret-injection-initcontainer.md` (new, covers the how/why, when to pick this pattern vs env-based secret injection, and the Prowlarr Job-based variant for list-of-objects)
  - Smoke test: for each app, rotate a sensitive 1P item, kick ESO sync, `kubectl scale deploy <name> -n media --replicas=0 && sleep 5 && kubectl scale deploy <name> -n media --replicas=1` (per `CLAUDE.md` operational warning), confirm new value is in the merged config, confirm the app still starts and functions.
  - Purpose: Hand-off documentation and production-readiness confidence
  - _Leverage: existing `docs/runbooks/` structure_
  - _Requirements: 4.1, 4.2, 5.3_

- [ ] 11. Public-repo leak gate (the "don't advertise my accounts" guardrail)
  - File: `.github/workflows/leak-scan.yml` (new)
  - CI action that runs on every PR against main and fails the build if any of the following are found in changed files OR the full rendered output from `make all` in `apps/media/`:
    - Known Usenet provider substrings (maintained as a block-list in `scripts/ci/provider-blocklist.txt`, not committed with real values but populated from the 1P inventory at CI time via a masked secret, or hashed for offline comparison).
    - Any email address matching `*@*` (with an allow-list for known-safe docs addresses).
    - Any hostname matching common Usenet TLD patterns (e.g. hosts under `.net` or `.com` inside `apps/media/nzbget/` rendered output, excluding the cluster-internal `*.svc.cluster.local`).
    - Tracker-style patterns (domain containing `tracker`, `torrent`, or `anounce`).
  - On block, emit a human-readable diff pointer so the author can see which file/line tripped it.
  - Purpose: Prevent an accidental future PR from putting the "who I pay" signal into Git. Paired with Requirement 1 criterion 6.
  - _Leverage: existing `.github/workflows/` patterns, pre-commit / `gitleaks` conventions_
  - _Requirements: 1.6_

- [ ] 12. History audit (one-shot, no scrub)
  - File: none (investigation). Output recorded in the PR body or an operator note, not a committed doc (to avoid a committed doc being the leak vector itself).
  - Run `git log --all -p | grep -iE '<provider-hostnames from 1P inventory>'` over the full k8s-argocd history. If hits found, decide on a case-by-case basis whether to `git filter-repo` (with the usual "this breaks forks" warning) or rotate-and-accept.
  - Currently believed clean based on the 2026-04-22 surface audit that preceded this spec; this task just confirms.
  - Purpose: Close the "maybe it leaked in the past" question one time.
  - _Leverage: `git log`, `git filter-repo` if needed_
  - _Requirements: 1.6 (retrospective)_
