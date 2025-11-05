#!/bin/bash
# 检测 AUR 包更新
# 对比当前 AUR 版本与 latest Release 中的版本

set -e

REPO_OWNER="${REPO_OWNER:-SkorionOS}"
REPO_NAME="${REPO_NAME:-skorion-packages}"
OUTPUT_FILE="${OUTPUT_FILE:-updated-packages.txt}"

echo "==> 检测 AUR 包更新"

# 下载 latest Release 的 packages.json
echo "==> 下载 latest Release 信息"
LATEST_JSON=$(curl -s "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/latest" || echo "{}")

# 检查是否存在 latest release
if echo "$LATEST_JSON" | jq -e '.message == "Not Found"' > /dev/null 2>&1; then
    echo "==> 首次构建，没有 latest Release"
    # 返回所有包
    grep -v '^#' aur.txt | grep -v '^$' > "$OUTPUT_FILE"
    TOTAL=$(wc -l < "$OUTPUT_FILE")
    echo "==> 所有 $TOTAL 个包将被构建"
    exit 0
fi

# 下载 packages.json
PACKAGES_JSON_URL=$(echo "$LATEST_JSON" | jq -r '.assets[] | select(.name == "packages.json") | .browser_download_url')
if [ -z "$PACKAGES_JSON_URL" ] || [ "$PACKAGES_JSON_URL" = "null" ]; then
    echo "==> 警告: 未找到 packages.json，构建所有包"
    grep -v '^#' aur.txt | grep -v '^$' > "$OUTPUT_FILE"
    exit 0
fi

echo "==> 下载 packages.json: $PACKAGES_JSON_URL"
curl -sL "$PACKAGES_JSON_URL" -o old-packages.json

# 创建旧版本映射
echo "==> 分析旧版本"
declare -A OLD_VERSIONS

while IFS= read -r pkg_full; do
    # 格式: packagename-1.2.3-1-x86_64
    # 提取包名和版本
    if [[ "$pkg_full" =~ ^(.+)-([0-9].+)-([0-9]+)-([^-]+)$ ]]; then
        pkg_name="${BASH_REMATCH[1]}"
        pkg_ver="${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
        OLD_VERSIONS[$pkg_name]="$pkg_ver"
        echo "  旧: $pkg_name = $pkg_ver"
    fi
done < <(jq -r '.packages[]' old-packages.json)

# 检查每个 AUR 包的当前版本
echo ""
echo "==> 检查 AUR 包当前版本"
: > "$OUTPUT_FILE"  # 清空输出文件

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
            echo "$pkg_name" >> "$OUTPUT_FILE"
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
        echo "$pkg_name" >> "$OUTPUT_FILE"
    elif [ "$current_ver" != "$old_ver" ]; then
        echo "  → 版本变化: $old_ver → $current_ver，需要构建"
        echo "$pkg_name" >> "$OUTPUT_FILE"
    else
        echo "  → 版本未变化，跳过"
    fi
done < <(grep -v '^#' aur.txt | grep -v '^$')

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
        if ! grep -q "^${pkg_name}$" "$OUTPUT_FILE"; then
            echo "$pkg_name" >> "$OUTPUT_FILE"
        fi
    done < aur-pinned.txt
fi

# 输出结果
UPDATED_COUNT=$(wc -l < "$OUTPUT_FILE" | tr -d ' ')
TOTAL_COUNT=$(grep -v '^#' aur.txt | grep -v '^$' | wc -l | tr -d ' ')

echo ""
echo "========================================"
echo "==> 检测完成"
echo "==> 需要构建: $UPDATED_COUNT / $TOTAL_COUNT 个包"
echo "========================================"

if [ "$UPDATED_COUNT" -eq 0 ]; then
    echo "==> 所有包都是最新的"
fi

cat "$OUTPUT_FILE"

