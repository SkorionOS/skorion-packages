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

# 使用 SRCDEST 环境变量（如果设置了）来缓存源码
export SRCDEST="${SRCDEST:-}"

# Setup build environment
bash /workspace/scripts/setup-archlinux-build-env.sh "${PACKAGE_NAME}"

# Execute build as builder user
echo "==> Building ${PACKAGE_TYPE} package: ${PACKAGE_NAME}"
if [ -n "$SRCDEST" ]; then
  echo "==> 使用源码缓存目录: $SRCDEST"
  # 确保 builder 用户有权限访问缓存目录
  mkdir -p "$SRCDEST"
  chown -R builder:builder "$SRCDEST"
fi

sudo -u builder bash -c "
  export OUTPUT_DIR='${OUTPUT_DIR}'
  export WORK_DIR='${WORK_DIR}'
  export GITHUB_WORKSPACE='${GITHUB_WORKSPACE}'
  export SRCDEST='${SRCDEST}'
  bash ${GITHUB_WORKSPACE}/scripts/build-single-package.sh '${PACKAGE_NAME}' '${PACKAGE_TYPE}'
"

echo "✓ Build complete"

