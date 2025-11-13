#!/bin/bash
# 清理 GitHub Release 中的旧版本包
# 支持两种自动检测模式：
#   1. 基于本地包：如果 OUTPUT_DIR 中有包文件，只清理这些包的旧版本
#   2. 扫描重复版本：如果没有本地包，扫描 release 中所有重复的包并只保留最新版本
#
# 环境变量：
#   DRY_RUN=true  - 只检测不删除，显示详细的保留/删除计划

set -euo pipefail

GITHUB_TOKEN="${GITHUB_TOKEN}"
REPO_FULL="${REPO_FULL}"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
RELEASE_TAG="${RELEASE_TAG:-latest}"
DRY_RUN="${DRY_RUN:-false}"

echo "==> 清理脚本启动"
echo "    REPO_FULL: $REPO_FULL"
echo "    RELEASE_TAG: $RELEASE_TAG"
echo "    DRY_RUN: $DRY_RUN"
echo ""

# 检测版本比较工具
if command -v vercmp > /dev/null 2>&1 && vercmp 1.0 2.0 > /dev/null 2>&1; then
    VERSION_COMPARE="vercmp"
    echo "使用 vercmp 进行版本比较"
else
    VERSION_COMPARE="sort"
    echo "使用 sort -V 进行版本比较"
fi

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

# 检测是否有本地包，自动选择清理模式
LOCAL_PKG_COUNT=0
for file in "$OUTPUT_DIR"/*.pkg.tar.zst; do
    [ -f "$file" ] && LOCAL_PKG_COUNT=$((LOCAL_PKG_COUNT + 1))
done

if [ $LOCAL_PKG_COUNT -gt 0 ]; then
    echo "  模式: 基于本地包清理（发现 $LOCAL_PKG_COUNT 个新包）"
    CLEAN_MODE="local"
else
    echo "  模式: 扫描重复版本清理（无本地包）"
    CLEAN_MODE="duplicates"
fi

# ============================================================================
# 模式 1: 基于本地包清理
# ============================================================================
if [ "$CLEAN_MODE" = "local" ]; then
    # 获取本地新构建的包名列表和完整文件名
    declare -A NEW_PACKAGES
    declare -A NEW_PACKAGE_FILES

    echo "  收集本地新包信息..."
    for file in "$OUTPUT_DIR"/*.pkg.tar.zst; do
        [ -f "$file" ] || continue
        
        filename=$(basename "$file")
        pkg_name=$(extract_package_name "$filename")
        
        if [ -n "$pkg_name" ]; then
            NEW_PACKAGES[$pkg_name]=1
            NEW_PACKAGE_FILES[$filename]=1
            echo "    新包: $pkg_name"
        fi
    done

    # 遍历 release 中的所有包文件，删除有新版本的旧包
    echo "  检查 release 中的现有文件..."

    deleted_count=0
    kept_count=0
    skipped_count=0

    # 避免使用管道（会创建子shell），使用进程替换
    while IFS='|' read -r asset_id asset_name; do
        pkg_name=$(extract_package_name "$asset_name")
        
        # 如果本地有相同的文件名，跳过（版本相同）
        if [ "${NEW_PACKAGE_FILES[$asset_name]:-}" = "1" ]; then
            echo "    = 跳过相同版本: $asset_name"
            skipped_count=$((skipped_count + 1))
        # 如果这个包有新版本（但文件名不同），删除旧的 asset
        elif [ "${NEW_PACKAGES[$pkg_name]:-}" = "1" ]; then
            echo "    ✗ 删除旧版本: $asset_name"
            
            http_code=$(curl -X DELETE \
                -H "Authorization: token $GITHUB_TOKEN" \
                "https://api.github.com/repos/${REPO_FULL}/releases/assets/$asset_id" \
                -w "%{http_code}" -o /dev/null -s)
            
            if [ "$http_code" = "204" ]; then
                deleted_count=$((deleted_count + 1))
            else
                echo "      警告: 删除失败 (HTTP $http_code)"
            fi
            
            # 避免 API 限流
            sleep 0.3
        else
            echo "    ✓ 保留: $asset_name (无新版本)"
            kept_count=$((kept_count + 1))
        fi
    done < <(echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | endswith(".pkg.tar.zst")) | "\(.id)|\(.name)"')

    echo ""
    echo "==> 清理完成"
    echo "    删除: $deleted_count 个旧版本"
    echo "    跳过: $skipped_count 个相同版本"
    echo "    保留: $kept_count 个无更新的包"

# ============================================================================
# 模式 2: 扫描重复版本清理
# ============================================================================
else
    if [ "$DRY_RUN" = "true" ]; then
        echo "==> Dry Run 模式：检测重复版本（不会执行删除）"
    fi
    
    # 收集所有包及其信息（包名 -> 版本列表）
    declare -A PACKAGE_VERSIONS
    
    echo "  扫描 release 中的所有包..."
    while IFS='|' read -r asset_id asset_name created_at; do
        pkg_name=$(extract_package_name "$asset_name")
        
        if [ -n "$pkg_name" ]; then
            # 追加到该包名的版本列表（使用 :- 避免 set -u 报错）
            if [ -n "${PACKAGE_VERSIONS[$pkg_name]:-}" ]; then
                PACKAGE_VERSIONS[$pkg_name]="${PACKAGE_VERSIONS[$pkg_name]} ${asset_id}:${asset_name}:${created_at}"
            else
                PACKAGE_VERSIONS[$pkg_name]="${asset_id}:${asset_name}:${created_at}"
            fi
        fi
    done < <(echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | endswith(".pkg.tar.zst")) | "\(.id)|\(.name)|\(.created_at)"')
    
    deleted_count=0
    kept_count=0
    
    # 对每个包名，找出最新版本并删除旧版本
    for pkg_name in "${!PACKAGE_VERSIONS[@]}"; do
        versions="${PACKAGE_VERSIONS[$pkg_name]}"
        # shellcheck disable=SC2206
        version_array=($versions)
        
        # 如果只有一个版本，跳过
        if [ ${#version_array[@]} -le 1 ]; then
            kept_count=$((kept_count + ${#version_array[@]}))
            continue
        fi
        
        if [ "$DRY_RUN" = "true" ]; then
            echo ""
            echo "📦 $pkg_name (${#version_array[@]} 个版本):"
        else
            echo "  发现 $pkg_name 有 ${#version_array[@]} 个版本，比较版本号保留最新"
        fi
        
        # 按版本号比较，找出最新的
        latest_version=""
        latest_asset_id=""
        latest_asset_name=""
        
        for version_info in "${version_array[@]}"; do
            IFS=':' read -r asset_id asset_name created_at <<< "$version_info"
            
            # 从文件名中提取版本号 (epoch:pkgver-pkgrel)
            # 格式: pkgname-[epoch--]pkgver-pkgrel-arch.pkg.tar.zst
            pkg_full="${asset_name%.pkg.tar.zst}"
            
            # 从右往左提取 arch 和 pkgrel
            if [[ "$pkg_full" =~ ^(.+)-([^-]+)-([^-]+)$ ]]; then
                pkg_with_ver="${BASH_REMATCH[1]}"
                pkgrel="${BASH_REMATCH[2]}"
                
                # 检查是否有 epoch（格式：packagename-epoch--pkgver）
                if [[ "$pkg_with_ver" =~ ^(.+)-([0-9]+)--(.+)$ ]]; then
                    epoch="${BASH_REMATCH[2]}"
                    pkgver="${BASH_REMATCH[3]}"
                    current_version="${epoch}:${pkgver}-${pkgrel}"
                elif [[ "$pkg_with_ver" =~ ^(.+)-(.+)$ ]]; then
                    pkgver="${BASH_REMATCH[2]}"
                    current_version="${pkgver}-${pkgrel}"
                else
                    echo "      警告: 无法解析版本 $asset_name，跳过"
                    continue
                fi
            else
                echo "      警告: 无法解析文件名 $asset_name，跳过"
                continue
            fi
            
            if [ -z "$latest_version" ]; then
                # 第一个版本
                latest_version="$current_version"
                latest_asset_id="$asset_id"
                latest_asset_name="$asset_name"
            else
                # 比较版本号
                if [ "$VERSION_COMPARE" = "vercmp" ]; then
                    # 使用 vercmp (pacman 的版本比较，最准确)
                    # vercmp 返回: 1 (第一个更新), 0 (相同), -1 (第二个更新)
                    cmp_result=$(vercmp "$current_version" "$latest_version")
                    if [ "$cmp_result" = "1" ]; then
                        latest_version="$current_version"
                        latest_asset_id="$asset_id"
                        latest_asset_name="$asset_name"
                    fi
                else
                    # 使用 sort -V (通用版本号排序)
                    newer=$(printf "%s\n%s\n" "$latest_version" "$current_version" | sort -V -r | head -1)
                    if [ "$newer" = "$current_version" ]; then
                        latest_version="$current_version"
                        latest_asset_id="$asset_id"
                        latest_asset_name="$asset_name"
                    fi
                fi
            fi
        done
        
        # 显示和处理所有版本
        for version_info in "${version_array[@]}"; do
            IFS=':' read -r asset_id asset_name created_at <<< "$version_info"
            
            # 重新提取版本号用于显示
            pkg_full="${asset_name%.pkg.tar.zst}"
            if [[ "$pkg_full" =~ ^(.+)-([^-]+)-([^-]+)$ ]]; then
                pkg_with_ver="${BASH_REMATCH[1]}"
                pkgrel="${BASH_REMATCH[2]}"
                if [[ "$pkg_with_ver" =~ ^(.+)-([0-9]+)--(.+)$ ]]; then
                    display_version="${BASH_REMATCH[2]}:${BASH_REMATCH[3]}-${pkgrel}"
                elif [[ "$pkg_with_ver" =~ ^(.+)-(.+)$ ]]; then
                    display_version="${BASH_REMATCH[2]}-${pkgrel}"
                else
                    display_version="unknown"
                fi
            else
                display_version="unknown"
            fi
            
            if [ "$asset_id" = "$latest_asset_id" ]; then
                # 保留
                if [ "$DRY_RUN" = "true" ]; then
                    echo "  ✅ 保留: $asset_name (版本: $display_version)"
                else
                    echo "    ✓ 保留: $asset_name (版本: $display_version)"
                fi
                kept_count=$((kept_count + 1))
            else
                # 删除
                if [ "$DRY_RUN" = "true" ]; then
                    echo "  ❌ 删除: $asset_name (版本: $display_version)"
                else
                    echo "    ✗ 删除: $asset_name"
                    
                    http_code=$(curl -X DELETE \
                        -H "Authorization: token $GITHUB_TOKEN" \
                        "https://api.github.com/repos/${REPO_FULL}/releases/assets/$asset_id" \
                        -w "%{http_code}" -o /dev/null -s)
                    
                    if [ "$http_code" = "204" ]; then
                        deleted_count=$((deleted_count + 1))
                    else
                        echo "      警告: 删除失败 (HTTP $http_code)"
                    fi
                    
                    sleep 0.3
                fi
            fi
        done
    done
    
    echo ""
    if [ "$DRY_RUN" = "true" ]; then
        echo "==> Dry Run 完成（未执行任何删除）"
        echo "    上述标记 ❌ 的版本将在实际运行时被删除"
        echo "    保留版本数: $kept_count"
    else
        echo "==> 清理完成"
        echo "    删除: $deleted_count 个旧版本"
        echo "    保留: $kept_count 个最新版本"
    fi
fi

