#!/bin/bash
# 检测 AUR 包和本地包更新
# 对比当前版本与 latest Release 中的版本

set -e

REPO_OWNER="${REPO_OWNER:-SkorionOS}"
REPO_NAME="${REPO_NAME:-skorion-packages}"
AUR_OUTPUT_FILE="${AUR_OUTPUT_FILE:-updated-aur-packages.txt}"
LOCAL_OUTPUT_FILE="${LOCAL_OUTPUT_FILE:-updated-local-packages.txt}"

echo "==> 检测包更新"

# 下载 latest Release 的 packages.json
echo "==> 下载 latest Release 信息"
LATEST_JSON=$(curl -s "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/latest" || echo "{}")

# 检查是否存在 latest release
FIRST_BUILD=false
if echo "$LATEST_JSON" | jq -e '.message == "Not Found"' > /dev/null 2>&1; then
    echo "==> 首次构建，没有 latest Release"
    FIRST_BUILD=true
fi

# 下载 packages.json
if [ "$FIRST_BUILD" = false ]; then
    PACKAGES_JSON_URL=$(echo "$LATEST_JSON" | jq -r '.assets[] | select(.name == "packages.json") | .browser_download_url')
    if [ -z "$PACKAGES_JSON_URL" ] || [ "$PACKAGES_JSON_URL" = "null" ]; then
        echo "==> 警告: 未找到 packages.json"
        FIRST_BUILD=true
    fi
fi

# 解析旧版本
declare -A OLD_VERSIONS

if [ "$FIRST_BUILD" = false ]; then
    echo "==> 下载 packages.json: $PACKAGES_JSON_URL"
    curl -sL "$PACKAGES_JSON_URL" -o old-packages.json
    
    # 创建旧版本映射
    echo "==> 分析旧版本"
    
    while IFS= read -r pkg_full; do
        # 格式: packagename-[epoch:]pkgver-pkgrel-arch
        # 从右往左解析，先去掉 arch 和 pkgrel，再提取 pkgver（可能包含 epoch）
        
        # 去掉扩展名（如果有）
        pkg_full="${pkg_full%.pkg.tar.*}"
        
        # 从右往左匹配: 最后一个 - 后面是 arch
        if [[ "$pkg_full" =~ ^(.+)-([^-]+)$ ]]; then
            pkg_without_arch="${BASH_REMATCH[1]}"
            
            # 再匹配一次: 最后一个 - 后面是 pkgrel
            if [[ "$pkg_without_arch" =~ ^(.+)-([0-9]+)$ ]]; then
                pkg_with_ver="${BASH_REMATCH[1]}"
                pkgrel="${BASH_REMATCH[2]}"
                
                # 再匹配一次: 最后一个 - 后面是 pkgver（可能包含 epoch 或 v 前缀）
                if [[ "$pkg_with_ver" =~ ^(.+)-(.+)$ ]]; then
                    pkg_name="${BASH_REMATCH[1]}"
                    pkgver="${BASH_REMATCH[2]}"
                    pkg_ver="${pkgver}-${pkgrel}"
                    OLD_VERSIONS[$pkg_name]="$pkg_ver"
                    echo "  旧: $pkg_name = $pkg_ver"
                fi
            fi
        fi
    done < <(jq -r '.packages[]' old-packages.json)
fi

# ==============================================================================
# 检测 AUR 包更新
# ==============================================================================
echo ""
echo "==> 检查 AUR 包"
: > "$AUR_OUTPUT_FILE"

if [ "$FIRST_BUILD" = true ]; then
    echo "==> 首次构建，构建所有 AUR 包"
    grep -v '^#' aur.txt | grep -v '^$' > "$AUR_OUTPUT_FILE"
else
    echo "==> 增量检测 AUR 包"
    while IFS= read -r pkg_name; do
    [ -z "$pkg_name" ] && continue
    
    echo "==> 检查: $pkg_name"
    
    # 获取 AUR 包信息
    AUR_INFO=$(curl -s "https://aur.archlinux.org/rpc/v5/info?arg[]=${pkg_name}")
    
    if echo "$AUR_INFO" | jq -e '.resultcount == 0' > /dev/null 2>&1; then
        echo "  警告: 在 AUR 未找到 $pkg_name，跳过"
        continue
    fi
    
    # 下载 PKGBUILD 检查是否有 pkgver() 函数
    PKGBUILD_URL=$(echo "$AUR_INFO" | jq -r '.results[0].URLPath')
    if [ -n "$PKGBUILD_URL" ] && [ "$PKGBUILD_URL" != "null" ]; then
        PKGBUILD_CONTENT=$(curl -s "https://aur.archlinux.org${PKGBUILD_URL}" | tar -xzO --wildcards '*/PKGBUILD' 2>/dev/null || echo "")
        
        if echo "$PKGBUILD_CONTENT" | grep -qE '^\s*pkgver\s*\(\)'; then
            echo "  → 动态版本包（有 pkgver() 函数），需要构建"
            echo "$pkg_name" >> "$AUR_OUTPUT_FILE"
            continue
        fi
    fi
    
    # 提取当前版本
    current_ver=$(echo "$AUR_INFO" | jq -r '.results[0].Version')
    echo "  当前: $current_ver"
    
    # 对比版本
    old_ver="${OLD_VERSIONS[$pkg_name]}"
    if [ -z "$old_ver" ]; then
        echo "  → 新包，需要构建"
        echo "$pkg_name" >> "$AUR_OUTPUT_FILE"
    elif [ "$current_ver" != "$old_ver" ]; then
        echo "  → 版本变化: $old_ver → $current_ver，需要构建"
        echo "$pkg_name" >> "$AUR_OUTPUT_FILE"
    else
        echo "  → 版本未变化，跳过"
    fi
    done < <(grep -v '^#' aur.txt | grep -v '^$')
fi

# 检查 pinned 包（始终构建）
if [ -f "aur-pinned.txt" ]; then
    echo ""
    echo "==> 检查固定版本包"
    while IFS='=' read -r pkg_name commit_hash; do
        # 跳过注释和空行
        [[ "$pkg_name" =~ ^#.*$ ]] && continue
        [ -z "$pkg_name" ] && continue
        
        echo "  → $pkg_name (pinned) 将被构建"
        # 确保不重复添加
        if ! grep -q "^${pkg_name}$" "$AUR_OUTPUT_FILE"; then
            echo "$pkg_name" >> "$AUR_OUTPUT_FILE"
        fi
    done < aur-pinned.txt
fi

# ==============================================================================
# 检测本地包更新
# ==============================================================================
echo ""
echo "==> 检查本地包"
: > "$LOCAL_OUTPUT_FILE"

if [ ! -d "local" ]; then
    echo "==> 无本地包目录"
elif [ "$FIRST_BUILD" = true ]; then
    echo "==> 首次构建，构建所有本地包"
    find local -mindepth 1 -maxdepth 1 -type d -exec basename {} \; > "$LOCAL_OUTPUT_FILE"
else
    echo "==> 增量检测本地包"
    for pkg_dir in local/*/; do
        pkg_name=$(basename "$pkg_dir")
        
        if [ ! -f "$pkg_dir/PKGBUILD" ]; then
            continue
        fi
        
        # 检查是否有 pkgver() 函数
        if grep -qE '^\s*pkgver\s*\(\)' "$pkg_dir/PKGBUILD"; then
            echo "  → $pkg_name 有 pkgver() 函数，需要构建"
            echo "$pkg_name" >> "$LOCAL_OUTPUT_FILE"
            continue
        fi
        
        # 提取 PKGBUILD 中的版本
        (
            source "$pkg_dir/PKGBUILD" 2>/dev/null || exit 1
            current_ver="${pkgver}-${pkgrel}"
            
            # 对比版本
            old_ver="${OLD_VERSIONS[$pkg_name]}"
            
            if [ -z "$old_ver" ]; then
                echo "  → $pkg_name 新包，需要构建"
                echo "$pkg_name" >> "$LOCAL_OUTPUT_FILE"
            elif [ "$current_ver" != "$old_ver" ]; then
                echo "  → $pkg_name 版本变化: $old_ver → $current_ver，需要构建"
                echo "$pkg_name" >> "$LOCAL_OUTPUT_FILE"
            else
                echo "  → $pkg_name 版本未变化，跳过"
            fi
        )
    done
fi

# 输出结果
AUR_COUNT=$(wc -l < "$AUR_OUTPUT_FILE" | tr -d ' ')
LOCAL_COUNT=$(wc -l < "$LOCAL_OUTPUT_FILE" | tr -d ' ')

echo ""
echo "========================================"
echo "==> 检测完成"
echo "==> AUR 包: $AUR_COUNT 个需要构建"
echo "==> 本地包: $LOCAL_COUNT 个需要构建"
echo "========================================"

