#!/usr/bin/env bash
set -euo pipefail

cd /root/PX4-Autopilot

export PX4_SIM_LOCKSTEP=1

NUM_DRONES="${NUM_DRONES:-1}"
BASE_INSTANCE="${PX4_BASE_INSTANCE:-0}"
DRONE_MODEL="${DRONE_MODEL:-gz_x500}"
BASE_EAST="${PX4_GZ_BASE_EAST:-0}"
BASE_NORTH="${PX4_GZ_BASE_NORTH:-0}"
ROOTFS_LOG_DIR="/root/PX4-Autopilot/build/px4_sitl_default/rootfs/log"
PX4_BIN="/root/PX4-Autopilot/build/px4_sitl_default/bin/px4"

for ((i=0; i<NUM_DRONES; i++)); do
  INSTANCE=$((BASE_INSTANCE + i))
  X_OFFSET=$((i * 3))
  POSE_X=$(awk "BEGIN { printf \"%.3f\", ${BASE_EAST} + ${X_OFFSET} }")
  POSE_Y=$(awk "BEGIN { printf \"%.3f\", ${BASE_NORTH} }")
  INSTANCE_LOG_DIR="${ROOTFS_LOG_DIR}/instance_${INSTANCE}"

  mkdir -p "${INSTANCE_LOG_DIR}"

  if [[ "${i}" -eq 0 ]]; then
    (
      cd /root/PX4-Autopilot
      : >"${INSTANCE_LOG_DIR}/px4_stdout.log"
      PX4_INSTANCE="${INSTANCE}" \
      PX4_SYS_AUTOSTART=4001 \
      PX4_SIM_MODEL="${DRONE_MODEL}" \
      PX4_GZ_MODEL_POSE="${POSE_X},${POSE_Y}" \
      HEADLESS=1 \
      make px4_sitl "${DRONE_MODEL}" \
        >>"${INSTANCE_LOG_DIR}/px4_stdout.log" 2>&1
    ) &
  else
    (
      cd /root/PX4-Autopilot
      INSTANCE_DIR="/tmp/instance_${INSTANCE}"
      rm -rf "${INSTANCE_DIR}"
      mkdir -p "${INSTANCE_DIR}"
      ln -sfn /root/PX4-Autopilot/build/px4_sitl_default/etc "${INSTANCE_DIR}/etc"
      ln -sfn "${INSTANCE_LOG_DIR}" "${INSTANCE_DIR}/log"
      : >"${INSTANCE_LOG_DIR}/px4_stdout.log"
      PX4_SYS_AUTOSTART=4001 \
      PX4_SIM_MODEL="${DRONE_MODEL}" \
      PX4_GZ_MODEL_POSE="${POSE_X},${POSE_Y}" \
      PX4_GZ_STANDALONE=1 \
      HEADLESS=1 \
      "${PX4_BIN}" -i "${INSTANCE}" -w "${INSTANCE_DIR}" \
        >>"${INSTANCE_LOG_DIR}/px4_stdout.log" 2>&1
    ) &
  fi
done

wait
