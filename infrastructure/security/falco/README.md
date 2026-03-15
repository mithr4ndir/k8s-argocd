# Falco

Runtime syscall-based threat detection for Kubernetes using modern eBPF driver.

## What It Does
- Monitors syscalls on all cluster nodes via eBPF
- Detects container escapes, privilege escalation, sensitive file access, shell spawning
- Routes alerts to Alertmanager via Falcosidekick, then to Discord
- Exposes metrics via ServiceMonitor for Prometheus scraping

## Verify
```bash
kubectl logs -n security -l app.kubernetes.io/name=falco | head -20
# Test with:
kubectl run test --image=alpine --rm -it -- cat /etc/shadow
```
