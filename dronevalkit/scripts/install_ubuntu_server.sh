#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Install dronevalkit dependencies on a fresh Ubuntu server.

Usage:
  scripts/install_ubuntu_server.sh [options]

Options:
  --user USER        Non-root user that should own the Poetry environment and
                     be added to the docker group. Defaults to $SUDO_USER, then
                     $USER.
  --with-dev         Install Poetry dev dependencies as well.
  --pull-image       Pre-pull the simulator Docker image.
  -h, --help         Show this help text.

This script installs:
  - system packages required to build/run the project
  - Docker Engine from Ubuntu packages
  - Poetry for the target user
  - project dependencies via Poetry

Run it from the repository root, preferably with sudo:
  sudo bash scripts/install_ubuntu_server.sh --user <your-username> --pull-image
EOF
}

TARGET_USER="${SUDO_USER:-${USER:-}}"
WITH_DEV=0
PULL_IMAGE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            TARGET_USER="${2:-}"
            shift 2
            ;;
        --with-dev)
            WITH_DEV=1
            shift
            ;;
        --pull-image)
            PULL_IMAGE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "${TARGET_USER}" ]]; then
    echo "Could not determine target user. Pass --user <name>." >&2
    exit 1
fi

if ! id "${TARGET_USER}" >/dev/null 2>&1; then
    echo "User does not exist: ${TARGET_USER}" >&2
    exit 1
fi

if [[ ! -f "pyproject.toml" ]]; then
    echo "Run this script from the repository root (pyproject.toml not found)." >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run with root privileges. Re-run with sudo." >&2
    exit 1
fi

REPO_ROOT="$(pwd)"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
POETRY_BIN="${TARGET_HOME}/.local/bin/poetry"
SIM_IMAGE="zackmurry/dronevalkit-sim:latest"

export DEBIAN_FRONTEND=noninteractive

echo "[1/6] Installing Ubuntu packages..."
apt-get update
apt-get install -y \
    ca-certificates \
    curl \
    git \
    build-essential \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    libffi-dev \
    libssl-dev \
    docker.io \
    docker-compose-v2

echo "[2/6] Enabling Docker..."
systemctl enable --now docker

echo "[3/6] Adding ${TARGET_USER} to docker group..."
usermod -aG docker "${TARGET_USER}"

echo "[4/6] Installing Poetry for ${TARGET_USER}..."
if [[ ! -x "${POETRY_BIN}" ]]; then
    sudo -H -u "${TARGET_USER}" bash -lc 'curl -sSL https://install.python-poetry.org | python3 -'
fi

echo "[5/6] Installing project dependencies with Poetry..."
POETRY_INSTALL_ARGS=(install --no-interaction)
if [[ "${WITH_DEV}" -eq 1 ]]; then
    POETRY_INSTALL_ARGS+=(--with dev)
fi

sudo -H -u "${TARGET_USER}" env MPLCONFIGDIR=/tmp/dronevalkit-mpl bash -lc \
    "cd '${REPO_ROOT}' && '${POETRY_BIN}' env use python3 && '${POETRY_BIN}' ${POETRY_INSTALL_ARGS[*]}"

echo "[6/6] Verifying installs..."
sudo -H -u "${TARGET_USER}" bash -lc \
    "cd '${REPO_ROOT}' && '${POETRY_BIN}' run python3 -c 'import dronevalkit; print(\"dronevalkit import ok\")'"
docker --version

if [[ "${PULL_IMAGE}" -eq 1 ]]; then
    echo "Pulling simulator image ${SIM_IMAGE}..."
    docker pull "${SIM_IMAGE}"
fi

cat <<EOF

Bootstrap complete.

Notes:
  - The user '${TARGET_USER}' has been added to the docker group.
  - You may need to log out and back in before non-sudo docker commands work.
  - Poetry binary: ${POETRY_BIN}
  - Repo root: ${REPO_ROOT}

Suggested smoke test:
  sudo -u ${TARGET_USER} -H bash -lc "cd '${REPO_ROOT}' && ${POETRY_BIN} run python3 experiments/run_experiments.py --help"
EOF
