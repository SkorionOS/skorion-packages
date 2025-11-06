#!/bin/bash
# 清理 GitHub Release 中的旧版本包
# 只保留每个包的最新版本

set -e

GITHUB_TOKEN="${GITHUB_TOKEN}"
REPO_FULL="${REPO_FULL}"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
RELEASE_TAG="${RELEASE_TAG:-latest}"

if [ -z "$GITHUB_TOKEN" ]; then
    echo "错误: 需要设置 GITHUB_TOKEN 环境变量"
    exit 1
fi

if [ -z "$REPO_FULL" ]; then
    echo "错误: 需要设置 REPO_FULL 环境变量 (格式: owner/repo)"
    exit 1
fi

echo "==> 清理 $RELEASE_TAG release 中的旧版本包"

# 获取 release 信息
RELEASE_JSON=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/${REPO_FULL}/releases/tags/${RELEASE_TAG}")

RELEASE_ID=$(echo "$RELEASE_JSON" | jq -r '.id')

if [ "$RELEASE_ID" = "null" ] || [ -z "$RELEASE_ID" ]; then
    echo "  $RELEASE_TAG release 不存在，跳过清理"
    exit 0
fi

echo "  Release ID: $RELEASE_ID"

# 提取包名的函数（处理 epoch 等复杂情况）
extract_package_name() {
    local filename="$1"
    
    # 移除 .pkg.tar.zst
    filename="${filename%.pkg.tar.zst}"
    
    # 从右往左依次移除: arch -> pkgrel -> pkgver
    # 格式: packagename-[epoch-]pkgver-pkgrel-arch
    
    # 移除 arch (最后一个 -)
    if [[ "$filename" =~ ^(.+)-([^-]+)$ ]]; then
        filename="${BASH_REMATCH[1]}"
        
        # 移除 pkgrel (倒数第二个 -)
        if [[ "$filename" =~ ^(.+)-([0-9]+)$ ]]; then
            filename="${BASH_REMATCH[1]}"
            
            # 移除 pkgver (可能包含 epoch，格式是 epoch--version)
            if [[ "$filename" =~ ^(.+)-([0-9]+)--(.+)$ ]]; then
                # 有 epoch: packagename-epoch--version
                echo "${BASH_REMATCH[1]}"
            elif [[ "$filename" =~ ^(.+)-(.+)$ ]]; then
                # 无 epoch: packagename-version
                echo "${BASH_REMATCH[1]}"
            else
                echo "$filename"
            fi
        else
            echo "$filename"
        fi
    else
        echo "$filename"
    fi
}

# 获取本地新构建的包名列表
declare -A NEW_PACKAGES

echo "  检测本地新构建的包..."
for file in "$OUTPUT_DIR"/*.pkg.tar.zst; do
    [ -f "$file" ] || continue
    
    filename=$(basename "$file")
    pkg_name=$(extract_package_name "$filename")
    
    if [ -n "$pkg_name" ]; then
        NEW_PACKAGES[$pkg_name]=1
        echo "    新包: $pkg_name"
    fi
done

if [ ${#NEW_PACKAGES[@]} -eq 0 ]; then
    echo "  没有新包，跳过清理"
    exit 0
fi

# 遍历 release 中的所有包文件，删除有新版本的旧包
echo "  检查 release 中的现有文件..."

deleted_count=0
kept_count=0

echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | endswith(".pkg.tar.zst")) | "\(.id)|\(.name)"' | \
while IFS='|' read -r asset_id asset_name; do
    pkg_name=$(extract_package_name "$asset_name")
    
    # 如果这个包有新版本，删除旧的 asset
    if [ "${NEW_PACKAGES[$pkg_name]}" = "1" ]; then
        echo "    ✗ 删除旧版本: $asset_name"
        
        http_code=$(curl -X DELETE \
            -H "Authorization: token $GITHUB_TOKEN" \
            "https://api.github.com/repos/${REPO_FULL}/releases/assets/$asset_id" \
            -w "%{http_code}" -o /dev/null -s)
        
        if [ "$http_code" = "204" ]; then
            ((deleted_count++))
        else
            echo "      警告: 删除失败 (HTTP $http_code)"
        fi
        
        # 避免 API 限流
        sleep 0.3
    else
        echo "    ✓ 保留: $asset_name (无新版本)"
        ((kept_count++))
    fi
done

echo ""
echo "==> 清理完成"
echo "    删除: $deleted_count 个旧版本"
echo "    保留: $kept_count 个包"

