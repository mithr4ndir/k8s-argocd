# Operational Runbooks

## K8s Node Lifecycle

### Draining a Node

Use when: VM restart, driver update, GPU recovery, host maintenance.

```bash
# 1. Prevent new pods from scheduling
kubectl cordon <node>

# 2. Evict existing pods (reschedule to other nodes)
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=60s

# 3. If a pod hangs during drain (e.g. StatefulSet with PDB)
kubectl delete pod -n <namespace> <pod> --grace-period=10

# 4. Perform maintenance...

# 5. Re-enable scheduling
kubectl uncordon <node>

# 6. Verify pods redistributed
kubectl get pods -A -o wide
```

### Node Considerations

| Node | Special Role | Drain Impact |
|------|-------------|--------------|
| k8cluster1 | General workloads | Media apps (sonarr, radarr, etc.) may move here |
| k8cluster2 | **GPU node** (RTX 2080 Ti) | Jellyfin loses hardware transcoding. Direct play still works on other nodes. |
| k8cluster3 | General workloads | Similar to k8cluster1 |

- All 3 nodes are control-plane — cluster stays healthy with 2/3 up (etcd quorum).
- DaemonSets (calico, kube-proxy, metallb-speaker, filebeat) are skipped by `--ignore-daemonsets` and auto-restart when the node comes back.

---

## GPU Recovery (RTX 2080 Ti)

### Symptoms
- `nvidia-smi` inside k8cluster2 returns "No devices found"
- Jellyfin shows "This client isn't compatible with the media" (transcoding fails)
- ffmpeg exits with code 187
- `dmesg` on k8cluster2 shows:
  - `NVRM: Xid 45` — GPU channel preemption (secondary symptom)
  - `RmInitAdapter failed! (0x23:0x65:1438)` — GPU stuck in dirty state

### Recovery via PCI Rescan (no host reboot)

```bash
# 1. Cordon + drain k8cluster2
kubectl cordon k8cluster2
kubectl drain k8cluster2 --ignore-daemonsets --delete-emptydir-data --timeout=180s

# 2. Shut down VM 109 on pve2
ssh root@192.168.1.11 'qm shutdown 109 --timeout 30'

# 3. Remove GPU from PCI bus and rescan
ssh root@192.168.1.11 'echo 1 > /sys/bus/pci/devices/0000:0a:00.0/remove'
sleep 2
ssh root@192.168.1.11 'echo 1 > /sys/bus/pci/rescan'
sleep 2

# 4. Verify GPU is back and bound to vfio-pci
ssh root@192.168.1.11 'lspci -nnk -s 0a:00.0 | grep "driver in use"'
# Expected: Kernel driver in use: vfio-pci

# 5. Start the VM
ssh root@192.168.1.11 'qm start 109'

# 6. Wait ~30s, verify GPU works inside VM
ssh ladino@192.168.1.89 'nvidia-smi'

# 7. Uncordon
kubectl uncordon k8cluster2

# 8. Verify Jellyfin lands back on k8cluster2
kubectl get pods -n media -o wide
```

### If PCI Rescan Doesn't Fix It
Full pve2 reboot is needed. **Warning**: cmd_center1 (VM 105) also runs on pve2 — you will lose SSH access during the reboot.

### Permanent Fixes (Applied)
- **nvidia driver upgraded to 570.211.01** (from 535.288.01) — includes RmInitAdapter fixes from 550+
- **GSP firmware disabled**: `options nvidia NVreg_EnableGpuFirmware=0` in `/etc/modprobe.d/nvidia.conf` inside k8cluster2
- **PVE hookscript**: `/var/lib/vz/snippets/gpu-reset.sh` on pve2 — auto PCI remove/rescan on VM 109 start/stop
  - Attached via: `qm set 109 --hookscript local:snippets/gpu-reset.sh`
  - Logs to: `/var/log/gpu-reset.log`

**Important**: `sudo reboot` from inside the VM does NOT trigger the hookscript. Always stop/start via PVE (`qm stop 109` / `qm start 109`) to ensure the GPU gets a clean PCI reset.

---

## Jellyfin

### Safe Restart
```bash
# Check no one is streaming first (Jellyfin Dashboard > Activity)
kubectl rollout restart deployment/jellyfin -n media
```

### Database Corruption Prevention
- **Never force-kill or restart the pod while someone is actively streaming.** Jellyfin's SQLite DB can corrupt if writes are interrupted mid-transaction.
- If you must restart, ask users to stop playback first, or wait for streams to end.

### Transcoding Not Working
1. Check which node Jellyfin is on:
   ```bash
   kubectl get pods -n media -o wide | grep jellyfin
   ```
   Must be on `k8cluster2` for GPU transcoding.

2. Check GPU inside the VM:
   ```bash
   ssh ladino@192.168.1.89 'nvidia-smi'
   ```
   If "No devices found", see GPU Recovery above.

3. Check ffmpeg errors:
   ```bash
   kubectl logs -n media <jellyfin-pod> | grep -iE "ffmpeg|nvenc|cuda|error"
   ```
   Exit code 187 = GPU initialization failure.

### Direct Play vs Transcoding
| Scenario | What Happens |
|----------|-------------|
| Client supports codec natively | Direct play — no GPU needed |
| Client needs different codec/resolution | GPU transcode (NVENC) |
| GPU is down, client needs transcode | **Fails** — Jellyfin won't fall back to CPU transcoding when GPU is configured |

### Client Buffering (Remote Streaming)
The server cannot push buffer settings to clients. Each client must be configured individually.

**Jellyfin Desktop (Windows/Mac/Linux):**
Add to MPV config (`%LOCALAPPDATA%\JellyfinMediaPlayer\mpv.conf` on Windows):
```
cache=yes
demuxer-max-bytes=500M
demuxer-readahead-secs=300
```
This buffers up to 5 minutes ahead, absorbing connection hiccups.

**LG webOS TV:**
No buffer control possible (platform limitation). The app hands playback to the TV's native player. Alternatives:
- [Moonfin](https://github.com/Moonfin-Client/Smart-TV) — actively maintained alternative Jellyfin client for webOS
- External streaming device (Fire Stick 4K Max, Apple TV) with better app support

**General remote streaming:**
- 1080p direct play needs ~10-15 Mbps
- 4K compressed needs ~25-40 Mbps
- 4K remux peaks at ~80-100 Mbps
- Most hotel WiFi is 20-50 Mbps — 1080p fine, 4K may need server-side bitrate cap

---

## Alertmanager / Discord Alerts

### Config Location
Alertmanager config is in a manually-managed K8s secret (not Helm values):
```bash
# View current config
kubectl get secret alertmanager-custom-config -n monitoring -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d

# Update config (edit the YAML, then apply)
kubectl create secret generic alertmanager-custom-config \
  -n monitoring \
  --from-file=alertmanager.yaml=alertmanager.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Why a secret instead of Helm values?** Prometheus Operator v0.77.2 doesn't support `webhook_url_file` for Discord configs, so the webhook URL must be in plaintext. Using a manually-managed secret keeps it out of the public git repo.

### Alert Rules Location
Alert rules are in `values.yaml` under `additionalPrometheusRulesMap`. Changes go through the normal ArgoCD flow (edit values.yaml → push → auto-sync).

### Testing Alerts
```bash
# Send a test alert to Alertmanager
kubectl exec -n monitoring alertmanager-kube-prometheus-stack-alertmanager-0 -- \
  amtool alert add test-alert severity=warning instance=test --alertmanager.url=http://localhost:9093
```
