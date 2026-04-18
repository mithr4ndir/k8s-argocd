# Tasks: Media Service Health Alerts

- [x] 1. Audit existing monitoring setup
  - Read kube-prometheus-stack values.yaml for PrometheusRule patterns
  - Check if Blackbox Exporter is deployed (it is not)
  - Confirm kube-state-metrics provides required metrics for all 7 media deployments
  - _Requirements: 1, 2, 3_

- [x] 2. Create MediaDeploymentUnavailable PrometheusRule
  - File: infrastructure/monitoring/kube-prometheus-stack/values.yaml (additionalPrometheusRulesMap)
  - Severity: critical, for: 5m
  - PromQL: `kube_deployment_status_replicas_available{namespace="media"} == 0 and kube_deployment_spec_replicas{namespace="media"} > 0`
  - _Requirements: 1_

- [x] 3. Create MediaPodCrashLooping PrometheusRule
  - Severity: warning, for: 10m
  - PromQL: `increase(kube_pod_container_status_restarts_total{namespace="media"}[1h]) > 5`
  - Annotations include pod name, container, and restart count
  - _Requirements: 2_

- [x] 4. Create MediaPodNotReady PrometheusRule
  - Severity: warning, for: 10m
  - PromQL: `kube_pod_status_ready{namespace="media", condition="true"} == 0 and kube_pod_status_phase{namespace="media", phase="Running"} == 1`
  - _Requirements: 3_

- [x] 5. Add Alertmanager route for media namespace
  - File: infrastructure/monitoring/alertmanager-config.yaml
  - Matcher: `namespace = "media"`, receiver: discord
  - group_by: [alertname], group_wait: 1m, repeat_interval: 1h
  - Placed before catch-all severity route
  - _Requirements: 4_

- [x] 6. Validate PromQL against live Prometheus
  - Confirmed all 7 deployments produce expected kube-state-metrics
  - No false positives from intentionally scaled-down deployments
  - _Requirements: 1, 2, 3_

- [ ] 7. Merge PR #139 and verify alerts appear in Prometheus
  - Confirm rules load in Prometheus UI (/rules)
  - Test by scaling a media deployment to 0, verify Discord alert fires
  - Verify resolved notification when scaling back to 1
  - _Requirements: 1, 4_

- [ ] 8. Follow-up: Application-level monitoring (future PR)
  - NZBGet queue depth and ServerPaused status via JSON-RPC scraping
  - Sonarr blocklist growth rate
  - Jellyseerr sync failure count
  - Requires custom exporter or Blackbox Exporter deployment
  - _Requirements: (future scope)_
