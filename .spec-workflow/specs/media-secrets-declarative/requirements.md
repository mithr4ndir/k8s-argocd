# Requirements Document

## Introduction

The media namespace apps (Jellyseerr, Sonarr, Radarr, Prowlarr, Jellyfin, Bazarr, NZBGet, qBittorrent) all store API keys, account credentials, and provider-identifying fields as plaintext inside runtime config files on the `k8s-nfs` RWX PVCs. None of it is managed in IaC.

This spec closes that gap with one mechanism: every sensitive field is sourced from 1Password via ESO, then injected at pod startup via a small `jq` / `xmlstarlet` initContainer that merges the fields into the existing config file without clobbering UI-managed state. UI-editable settings (library mappings, user accounts, permissions) remain in the PVC and are not touched.

**Scope boundary (what is "sensitive"):** Not just API keys. The `k8s-argocd` repo is public. The operator wants the presence of the downloader stack (Sonarr/Radarr/Prowlarr directories, image pulls) to be acceptable, but account identifiers, Usenet provider hostnames, indexer/tracker names, and any field that identifies which paid services are in use MUST NOT appear in Git, in rendered manifests, or in commit history. Concretely this covers: account usernames and email addresses, Usenet server hostnames (`news.example.com`, `eu.example.com`), server ports and SSL flags, account passwords and API keys, indexer display names and URLs (NZBgeek, Nzb.su, NzbPlanet, etc.), indexer API keys, tracker hostnames and cookies, RSS feed URLs containing tokens, download client auth.

**What stays in Git:** App directory names, bjw-s/app-template Helm values (image pins, resources, probes), namespace manifests, Jellyfin playback config, PVC definitions, ArgoCD Applications. The operator accepts that "this cluster runs the *arr stack" is derivable from these. The line is drawn at "who I pay and what accounts I use."

This is a direct execution of the homelab prime directive ("everything must be managed in code") applied to a surface that has been missed up to now. It ships after `eso-rate-limit-hardening` so the new ExternalSecret resources this adds cannot amplify a future rate-limit drain.

## Alignment with Product Vision

Prime directive ("everything managed in code, no manual configuration") + security-first directive ("never hardcode secrets... use secret managers"). Today the media stack stores API keys as cleartext on NFS. A NAS misconfiguration or a ransomware event on the volume leaks every key at once. Rotating a key means editing the config via each app's UI or by hand-editing the file, neither of which is IaC. This spec makes the rotation a single 1Password edit.

This also closes a known debt line: the ansible-side secret discipline (`op-secret-cache`, kill switch, vault patterns) has no equivalent on the k8s side for per-app secrets beyond ExternalSecret-to-Secret. The initContainer merge pattern extends that discipline to apps that do not support env-based secret injection.

## Requirements

### Requirement 1: ExternalSecrets for every sensitive field in the media stack

**User Story:** As an operator, I want every sensitive field across the media stack (API keys + provider/account identifiers + auth credentials) to exist as a 1Password item mirrored to a Kubernetes Secret, so that rotation is a single 1P edit and no such field ever lives in source, rendered manifests, or Git history.

#### Acceptance Criteria

1. WHEN the namespace is audited THEN every field matching the classification in the Introduction SHALL have a corresponding item in the `Infrastructure` 1Password vault. Minimum coverage at ship time: Jellyseerr own-API-key, Jellyfin API key, Sonarr API key, Radarr API key, Prowlarr API key, Bazarr API key, qBittorrent admin password, NZBGet control+add credentials, every Prowlarr indexer (name + URL + API key) stored together as a single JSON-blob 1P item, every Usenet provider server config (host + port + SSL + username + password) as one or more 1P items per provider.
2. WHEN the cluster is reconciled THEN one ExternalSecret per app SHALL exist mapping those 1P items into namespace-local Secrets. The ESO `remoteRef` SHALL use the `<item-id>/<field>` format documented in the root `CLAUDE.md`, not the `op://` URI style.
3. WHEN a 1P item is rotated THEN a manual ESO sync (per the existing operator feedback) SHALL propagate the new value within 60 seconds.
4. WHEN the spec ships THEN every sensitive field currently living in a config file SHALL be rotated once at cut-over (treat the pre-spec cleartext value as compromised).
5. WHEN new sensitive fields are added in the future (new indexer, new provider, new inter-app credential) THEN the pattern SHALL be extended by adding 1P items and updating the ExternalSecret allow-list, not by writing to config files.
6. WHEN `git grep` is run across the repo and its history after cut-over THEN it SHALL return zero hits for any Usenet provider hostname, any indexer display name present in Prowlarr, any account email/username, and any non-Kubernetes-internal URL in a downloader config. CI SHALL enforce this with a block-list gate on PRs.

### Requirement 2: initContainer merges secrets into config on every start

**User Story:** As the Jellyseerr pod (and Sonarr, Radarr, etc.), I need the secret fields in my config file to come from a Kubernetes Secret on every startup, so that UI-editable fields can still be managed via the app but credential fields remain authoritative in IaC.

#### Acceptance Criteria

1. WHEN a pod starts THEN an initContainer SHALL read the namespace-local Secret at `/secrets` and merge the relevant fields into the config file at `/config` using `jq` for JSON or `xmlstarlet` for XML.
2. WHEN the config file does not exist yet (first install) THEN the initContainer SHALL NOT create one; the main container runs the first-time setup wizard, and the merge only activates on subsequent starts.
3. WHEN the initContainer cannot produce a valid merged file (jq error, secret missing) THEN it SHALL exit non-zero so the main container never starts with a partially-merged file.
4. WHEN the merge completes THEN the only fields written SHALL be those listed in the per-app allow-list in the ConfigMap; all other fields in the config file SHALL be preserved byte-for-byte.
5. WHEN the merge runs THEN it SHALL take a dated backup of the pre-merge config file (e.g. `settings.json.pre-merge.<timestamp>`) kept for 7 days, so a bad merge is reversible.

### Requirement 3: Alignment with the rate-limit hardening spec

**User Story:** As an operator, I want the new ExternalSecrets added by this spec to honor the fleet-wide refresh-interval standards set in `eso-rate-limit-hardening`, so that rolling this out cannot itself create an incident.

#### Acceptance Criteria

1. WHEN any new ExternalSecret is authored by this spec THEN it SHALL declare `spec.refreshInterval: 24h`.
2. WHEN this spec is ready to ship THEN `eso-rate-limit-hardening` SHALL already be merged and its circuit breaker SHALL be operational in the cluster.
3. WHEN the additional ExternalSecrets are added THEN the projected steady-state read rate increase SHALL be documented in the PR body (scope widened from ~7 to ~12-15 secrets at 1 read/day each; confirm exact count in task 1 audit).

### Requirement 3b: Prowlarr indexer list handled as a single encrypted blob

**User Story:** As an operator, I want Prowlarr's entire indexer list (N indexers × {name, URL, API key, categories, priority}) to be sourced from a single 1P item at startup, so that the public repo never names any indexer and operators do not need to maintain N separate ExternalSecrets.

#### Acceptance Criteria

1. WHEN Prowlarr starts THEN its initContainer SHALL read a JSON blob from a Kubernetes Secret and POST each entry to the Prowlarr REST API (`/api/v1/indexer`) after Prowlarr's main container is healthy.
2. WHEN the blob is absent or empty THEN Prowlarr SHALL start normally with whatever indexers already exist in its SQLite DB; the sync is additive-idempotent, not authoritative.
3. WHEN an indexer is removed from the 1P blob THEN an operator-initiated cleanup command (documented in the runbook) SHALL be used to delete it from Prowlarr; automatic deletion is out of scope because it risks removing hand-tuned local settings.
4. WHEN Sonarr and Radarr query Prowlarr for their indexer feeds THEN they SHALL continue to use Prowlarr's built-in apps-sync (no independent indexer list maintained in Sonarr/Radarr configs).

### Requirement 4: Per-app secret inventory

**User Story:** As an operator, I want a single document that lists which secrets each media app consumes and which 1P item each maps to, so that auditing and rotation are straightforward.

#### Acceptance Criteria

1. WHEN this spec ships THEN `docs/runbooks/media-secrets-inventory.md` SHALL exist with one table row per (app, secret field, 1P item, Kubernetes Secret key).
2. WHEN a rotation is performed THEN the runbook SHALL describe the exact steps in order (edit 1P, kick ESO sync, delete pod, confirm new key via API smoke-test).
3. WHEN a new app is added THEN the PR adding it SHALL add a row to this table as part of the same PR, not a follow-up.

### Requirement 5: Readiness to generalize to a reusable component

**User Story:** As an operator, I want the initContainer pattern to be reusable so that adding it to Radarr, Sonarr, and any future app is a small diff, not a copy-paste of 150 lines.

#### Acceptance Criteria

1. WHEN the initContainer is implemented for Jellyseerr THEN its shell script SHALL be held in a per-app ConfigMap (not inlined in values.yaml) so the values file stays clean.
2. WHEN the same pattern is applied to a second app (Sonarr) THEN it SHALL reuse the same Kustomize component or bespoke-chart helper (TBD during design phase), with only the app-specific allow-list differing.
3. WHEN the pattern is documented in `docs/patterns/secret-injection-initcontainer.md` THEN a new operator SHALL be able to apply it to another app in under 30 minutes.

## Non-Functional Requirements

### Code Architecture and Modularity

- The merge script SHALL be a single shell function per file format (one for JSON, one for XML), kept under 40 lines each, vendored into every app's ConfigMap.
- The initContainer image SHALL be a pinned-by-digest image with `jq`, `xmlstarlet`, and `coreutils` only. Prefer `alpine/git`-style minimal images over bespoke.
- No new Helm charts. Extend existing bjw-s `app-template` values via `initContainers` and `configMaps`.
- No new CustomResourceDefinitions.

### Performance

- Merge completes in under 2 seconds per pod start.
- The initContainer SHALL NOT touch the network (no API calls, no DNS lookups beyond what the image needs for startup).

### Security

- The initContainer SHALL run as non-root with `readOnlyRootFilesystem: true` and `allowPrivilegeEscalation: false` unless the existing main container runs as root (Jellyseerr currently does, see `apps/media/jellyseer/values.yaml`).
- The mounted Secret SHALL be read-only.
- The config file SHALL remain owned by the main container's runtime UID after the merge.
- Pre-merge backups SHALL be pruned after 7 days to limit the blast radius of a stolen NFS volume.
- All new Secrets SHALL have `type: Opaque` and SHALL NOT set `stringData` in source (ExternalSecret handles the encoding).
- Rendered manifests generated by `make all` SHALL be scanned in CI for provider hostnames and account identifiers before commit (see Requirement 1 criterion 6). A leaked provider name in a rendered Kustomize output is as bad as a leaked value in the source chart.
- The pre-merge backup files (`settings.json.pre-merge.<timestamp>`) live on the NFS PVC and contain the unsanitized config. The 7-day retention and NFS-level ZFS snapshot policy together bound the exposure window.

### Operational constraints

- During cut-over, *arr apps MUST be restarted via the `scale 0 -> sleep 5 -> scale 1` pattern documented in the root `CLAUDE.md`, NOT via `kubectl rollout restart`. The `Recreate` strategy plus NFS file locks make rollout restart unsafe and have caused a SQLite corruption incident on 2026-03-15.
- The rendered output of `make all` MUST be committed along with values changes; the repo does not deploy Helm releases directly, ArgoCD reads the flat manifests.

### Reliability

- If the initContainer fails, the main container SHALL NOT start, to avoid a partially-merged file being persisted to disk.
- A restart of the main container SHALL re-run the initContainer; the merge SHALL be idempotent (running it twice on the same inputs produces the same output).

### Usability

- Operator runbook (Requirement 4) SHALL be accessible from the existing runbook index.
- Rotating a single key SHALL not require a spec-workflow-level change; it is an operational action documented in the runbook.
