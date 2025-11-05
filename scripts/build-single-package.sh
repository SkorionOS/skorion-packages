#!/bin/bash
# Build a single AUR or local package
# Usage: ./build-single-package.sh <package_name> <package_type>
#   package_type: "aur" or "local"

set -e
set -x

PACKAGE_NAME="$1"
PACKAGE_TYPE="$2"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
WORK_DIR="${WORK_DIR:-/tmp/skorion-build}"

if [ -z "$PACKAGE_NAME" ] || [ -z "$PACKAGE_TYPE" ]; then
    echo "Usage: $0 <package_name> <package_type>"
    echo "  package_type: aur or local"
    exit 1
fi

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"

echo "==> 构建包: $PACKAGE_NAME (类型: $PACKAGE_TYPE)"

# =============================================================================
# 构建 AUR 包
# =============================================================================
if [ "$PACKAGE_TYPE" = "aur" ]; then
    cd "$WORK_DIR"
    
    # 克隆 AUR 仓库
    if [ ! -d "$PACKAGE_NAME" ]; then
        echo "==> 克隆 AUR 包: $PACKAGE_NAME"
        git clone "https://aur.archlinux.org/${PACKAGE_NAME}.git"
    fi
    
    cd "$PACKAGE_NAME"
    git pull || true
    
    # 检查是否需要 pin 到特定版本
    if [ -f "$GITHUB_WORKSPACE/aur-pinned.txt" ]; then
        PIN_COMMIT=$(grep "^${PACKAGE_NAME}=" "$GITHUB_WORKSPACE/aur-pinned.txt" | cut -d'=' -f2 || true)
        if [ -n "$PIN_COMMIT" ]; then
            echo "==> Pin 到版本: $PIN_COMMIT"
            git checkout "$PIN_COMMIT"
        fi
    fi
    
    # 使用 pikaur 构建（自动处理依赖）
    echo "==> 使用 pikaur 构建 $PACKAGE_NAME"
    PKGDEST="$OUTPUT_DIR" MAKEFLAGS="-j$(nproc)" \
        pikaur --noconfirm -S -P PKGBUILD
    
# =============================================================================
# 构建本地包
# =============================================================================
elif [ "$PACKAGE_TYPE" = "local" ]; then
    PACKAGE_DIR="$GITHUB_WORKSPACE/local/$PACKAGE_NAME"
    
    if [ ! -d "$PACKAGE_DIR" ]; then
        echo "==> 错误: 本地包目录不存在: $PACKAGE_DIR"
        exit 1
    fi
    
    cd "$PACKAGE_DIR"
    
    # 本地包也使用 pikaur（自动处理依赖，包括 AUR）
    echo "==> 使用 pikaur 构建本地包: $PACKAGE_NAME"
    PKGDEST="$OUTPUT_DIR" MAKEFLAGS="-j$(nproc)" \
        pikaur --noconfirm -S -P PKGBUILD
else
    echo "==> 错误: 未知的包类型: $PACKAGE_TYPE"
    exit 1
fi

# =============================================================================
# 后处理
# =============================================================================
cd "$OUTPUT_DIR"

# 删除 debug 包
rm -f *-debug-*.pkg.tar.zst
echo "==> 已删除 debug 包"

# 移除 epoch (:) 从文件名（artifact 不支持）
for file in *:*.pkg.tar.zst; do
    [ -f "$file" ] || continue
    new_name="${file//:/--}"
    mv "$file" "$new_name"
    echo "==> 移除 epoch: $file -> $new_name"
done

echo "==> 构建完成: $PACKAGE_NAME"
ls -lh *.pkg.tar.zst 2>/dev/null || echo "警告: 未找到构建的包"

