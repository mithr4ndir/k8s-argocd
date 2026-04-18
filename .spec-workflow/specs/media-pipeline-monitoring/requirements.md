# Requirements: Media Service Health Alerts

## Introduction

Three separate failures in the media download pipeline went completely undetected: NZBGet paused for hours, Jellyseerr failed Jellyfin sync every 5 minutes, and Sonarr accumulated 203 blocklisted releases. Zero monitoring existed for the media namespace.

## Requirements

### Requirement 1: Deployment Availability Alerting

**User Story:** As a homelab operator, I want to be alerted when any media service goes down, so that I can investigate before content delivery is impacted.

#### Acceptance Criteria

1. WHEN a media deployment has 0 available replicas for 5+ minutes THEN a critical alert SHALL fire
2. IF a deployment is intentionally scaled to 0 THEN no alert SHALL fire (avoid false positives)
3. WHEN the deployment recovers THEN a resolved notification SHALL be sent

### Requirement 2: Crash Loop Detection

**User Story:** As a homelab operator, I want to be alerted when media pods are crash-looping, so that I can catch config or image issues early.

#### Acceptance Criteria

1. WHEN a container accumulates more than 5 restarts in a 1-hour window, sustained for 10 minutes THEN a warning alert SHALL fire
2. IF the alert fires THEN it SHALL include the pod name, container name, and restart count

### Requirement 3: Readiness Failure Detection

**User Story:** As a homelab operator, I want to be alerted when pods are running but not ready, so that I can catch application-level failures (like NZBGet pausing).

#### Acceptance Criteria

1. WHEN a pod is Running but not Ready for 10+ minutes THEN a warning alert SHALL fire
2. IF readiness probes are failing THEN the alert description SHALL suggest checking logs

### Requirement 4: Alert Routing

**User Story:** As a homelab operator, I want media alerts routed to Discord with themed embeds, so that they match my existing alerting UX.

#### Acceptance Criteria

1. WHEN a media alert fires THEN it SHALL route to Discord via the discord-alert-proxy
2. IF multiple media services fail simultaneously THEN alerts SHALL be grouped by alertname into a single embed
3. WHEN alerts repeat THEN the repeat interval SHALL be 1 hour (avoid spam)

## Non-Functional Requirements

### Implementation Constraints
- Use existing kube-state-metrics (no new exporters)
- Namespace-scoped rules: `namespace="media"` filter auto-covers new deployments
- Follow existing `additionalPrometheusRulesMap` pattern in kube-prometheus-stack values.yaml

## References
- GitHub Issue: #135
- PR: #139
- Incident: NZBGet paused, Jellyseerr sync failing, Sonarr blocklist growth, all undetected
