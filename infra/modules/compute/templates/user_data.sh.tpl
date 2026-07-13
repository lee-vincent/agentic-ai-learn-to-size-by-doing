#!/bin/bash
# Best-effort node bring-up: mount the shared FSx weight cache and record
# EFA/GPU status for later phases to check. Nothing here should block boot
# (serving-builder in Phase 2 owns actually running vLLM/Ray).
set -u
LOG=/var/log/user-data.log
exec > >(tee -a "$LOG") 2>&1
echo "=== user_data start: $(date -u) ==="

echo "--- node identity ---"
echo "node_index=${node_index}"
echo "gpu_instance_type=${gpu_instance_type}"

echo "--- EFA check ---"
if command -v fi_info >/dev/null 2>&1; then
  fi_info -p efa || echo "WARNING: fi_info ran but reported no efa provider"
else
  echo "WARNING: fi_info not found -- EFA installer may not be present on this AMI; verify against the AMI release notes before relying on it for NCCL/Ray collective traffic."
fi

echo "--- NVIDIA driver check ---"
nvidia-smi -L || echo "WARNING: nvidia-smi not available yet"

echo "--- FSx for Lustre mount ---"
FSX_DNS="${fsx_dns_name}"
FSX_MOUNT_NAME="${fsx_mount_name}"
MOUNT_POINT=/mnt/fsx-weights

mkdir -p "$MOUNT_POINT"

if ! command -v mount.lustre >/dev/null 2>&1; then
  echo "lustre-client not found, attempting install..."
  if command -v apt-get >/dev/null 2>&1; then
    . /etc/os-release || true
    apt-get update -y || true
    apt-get install -y "lustre-client-modules-$(uname -r)" lustre-client-utils || \
      apt-get install -y lustre-client || \
      echo "WARNING: could not install lustre client via apt; mount below will likely fail until it is installed manually (see AWS FSx for Lustre client install docs for this AMI/kernel)."
  fi
fi

if command -v mount.lustre >/dev/null 2>&1; then
  MOUNT_SPEC="$FSX_DNS@tcp:/$FSX_MOUNT_NAME"
  if ! grep -q "$MOUNT_POINT" /etc/fstab; then
    echo "$MOUNT_SPEC $MOUNT_POINT lustre defaults,noatime,flock,_netdev 0 0" >> /etc/fstab
  fi
  mount "$MOUNT_POINT" || echo "WARNING: mount of $MOUNT_SPEC at $MOUNT_POINT failed -- check FSx security group / lustre-client version."
else
  echo "WARNING: mount.lustre still unavailable; skipping FSx mount. Model weights will not be visible at $MOUNT_POINT until this is resolved."
fi

echo "=== user_data end: $(date -u) ==="
