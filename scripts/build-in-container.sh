#!/bin/bash
# Build package in Arch Linux container
# Usage: build-in-container.sh <package-name> <package-type>

set -e

PACKAGE_NAME="${1:-}"
PACKAGE_TYPE="${2:-}"

if [ -z "$PACKAGE_NAME" ] || [ -z "$PACKAGE_TYPE" ]; then
  echo "Usage: $0 <package-name> <package-type>"
  exit 1
fi

export OUTPUT_DIR="/workspace/output"
export WORK_DIR="/tmp/build"
export GITHUB_WORKSPACE="/workspace"

# Setup build environment
bash /workspace/scripts/setup-archlinux-build-env.sh

# Execute build as builder user
echo "==> Building ${PACKAGE_TYPE} package: ${PACKAGE_NAME}"
sudo -u builder bash -c "
  export OUTPUT_DIR='${OUTPUT_DIR}'
  export WORK_DIR='${WORK_DIR}'
  export GITHUB_WORKSPACE='${GITHUB_WORKSPACE}'
  bash ${GITHUB_WORKSPACE}/scripts/build-single-package.sh '${PACKAGE_NAME}' '${PACKAGE_TYPE}'
"

echo "✓ Build complete"

