# NVIDIA GPU Support for Kubernetes

This directory contains the Kubernetes manifests for NVIDIA GPU support, enabling hardware transcoding in Jellyfin.

## Prerequisites (Node-Level Setup)

Before deploying the Kubernetes resources, the following must be installed on the node(s) with NVIDIA GPUs:

### 1. Identify the GPU Node

Determine which node has the NVIDIA GPU:
```bash
# SSH to each node and check
lspci | grep -i nvidia
```

### 2. Install NVIDIA Drivers

On the GPU node (Ubuntu 24.04):
```bash
# Add NVIDIA driver PPA
sudo add-apt-repository ppa:graphics-drivers/ppa -y
sudo apt update

# Install recommended driver (or specific version)
sudo ubuntu-drivers autoinstall
# OR specific version:
# sudo apt install nvidia-driver-550

# Reboot
sudo reboot

# Verify after reboot
nvidia-smi
```

### 3. Install NVIDIA Container Toolkit

On the GPU node:
```bash
# Add NVIDIA Container Toolkit repo
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit

# Configure containerd
sudo nvidia-ctk runtime configure --runtime=containerd
sudo systemctl restart containerd
```

### 4. Label the GPU Node

```bash
kubectl label nodes <GPU_NODE_NAME> nvidia.com/gpu=true
```

## Kubernetes Resources

### NVIDIA Device Plugin

The device plugin exposes GPUs to the Kubernetes scheduler:
- `device-plugin.yaml` - DaemonSet that runs on GPU nodes
- Node selector ensures it only runs on labeled GPU nodes

### RuntimeClass (Optional)

If using the NVIDIA runtime class:
- `runtime-class.yaml` - Defines nvidia RuntimeClass

## Deployment

Once prerequisites are met:

```bash
# Apply NVIDIA infrastructure
kubectl apply -k infrastructure/nvidia/

# Verify GPU is available
kubectl describe nodes | grep nvidia.com/gpu
# Should show:
# Allocatable:
#   nvidia.com/gpu: 1
```

## Jellyfin GPU Configuration

After GPU is available, Jellyfin needs:
1. GPU resource request in deployment
2. Proper environment variables for NVENC

See `apps/media/jellyfin/` for the updated configuration.

## Verification

```bash
# Check device plugin is running
kubectl -n kube-system get pods -l app=nvidia-device-plugin

# Check GPU resources on node
kubectl describe node <GPU_NODE_NAME> | grep -A5 nvidia

# Check Jellyfin can see GPU (after deployment)
kubectl -n media exec deployment/jellyfin -- nvidia-smi
```

## Troubleshooting

### GPU Not Detected
1. Verify `nvidia-smi` works on the host
2. Check containerd is configured: `cat /etc/containerd/config.toml | grep nvidia`
3. Restart containerd: `sudo systemctl restart containerd`

### Device Plugin CrashLooping
1. Check logs: `kubectl -n kube-system logs -l app=nvidia-device-plugin`
2. Verify node has GPU and drivers installed

### Jellyfin Can't Access GPU
1. Verify resource request is in deployment
2. Check pod is scheduled on GPU node
3. Verify NVENC is enabled in Jellyfin dashboard: Playback → Transcoding
