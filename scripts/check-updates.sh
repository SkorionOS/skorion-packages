#!/bin/bash
# 检测 AUR 包和本地包更新
# 对比当前版本与 latest Release 中的版本
#
# 环境变量:
#   REPO_OWNER          - GitHub 仓库所有者
#   REPO_NAME           - 仓库名称
#   FORCE_REBUILD       - 强制重建的包列表(逗号分隔)
#   LOG_LEVEL           - 日志级别: DEBUG, INFO(默认), WARN, ERROR
#
# 使用示例:
#   LOG_LEVEL=DEBUG bash scripts/check-updates.sh
#   FORCE_REBUILD=package1,package2 bash scripts/check-updates.sh

set -e

REPO_OWNER="${REPO_OWNER:-SkorionOS}"
REPO_NAME="${REPO_NAME:-skorion-packages}"
AUR_OUTPUT_FILE="${AUR_OUTPUT_FILE:-updated-aur-packages.txt}"
LOCAL_OUTPUT_FILE="${LOCAL_OUTPUT_FILE:-updated-local-packages.txt}"
FORCE_REBUILD="${FORCE_REBUILD:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"  # DEBUG, INFO, WARN, ERROR

# ==============================================================================
# 日志函数
# ==============================================================================
log_debug() {
    [[ "$LOG_LEVEL" == "DEBUG" ]] && echo "  [DEBUG] $*" >&2
}

log_info() {
    [[ "$LOG_LEVEL" =~ ^(DEBUG|INFO)$ ]] && echo "  $*" >&2
}

log_warn() {
    [[ "$LOG_LEVEL" =~ ^(DEBUG|INFO|WARN)$ ]] && echo "  ⚠ $*" >&2
}

log_error() {
    echo "  ✗ $*" >&2
}

log_success() {
    echo "  ✓ $*" >&2
}

log_header() {
    echo "==> $*"
}

export -f log_debug log_info log_warn log_error log_success log_header

log_header "检测包更新"

# 处理强制重建的包列表
FORCE_REBUILD_LIST=$(mktemp)
if [ -n "$FORCE_REBUILD" ]; then
    echo "==> 强制重建包列表: $FORCE_REBUILD"
    IFS=',' read -ra FORCE_PACKAGES <<< "$FORCE_REBUILD"
    for pkg in "${FORCE_PACKAGES[@]}"; do
        pkg=$(echo "$pkg" | xargs)  # 去除空格
        if [ -n "$pkg" ]; then
            echo "$pkg" >> "$FORCE_REBUILD_LIST"
            echo "  - $pkg (强制重建)"
        fi
    done
fi
export FORCE_REBUILD_LIST

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
        # 格式: packagename-[epoch-]pkgver-pkgrel-arch
        # 注意: epoch 中的 : 在文件名中被替换为 -，例如 5: 变成 5-
        # 所以 linuxqq-5:3.2.21-1 会变成 linuxqq-5--3.2.21-1（双连字符）
        
        # 去掉扩展名（如果有）
        pkg_full="${pkg_full%.pkg.tar.*}"
        
        # 从右往左匹配: 最后一个 - 后面是 arch
        if [[ "$pkg_full" =~ ^(.+)-([^-]+)$ ]]; then
            pkg_without_arch="${BASH_REMATCH[1]}"
            
            # 再匹配一次: 最后一个 - 后面是 pkgrel
            if [[ "$pkg_without_arch" =~ ^(.+)-([0-9]+)$ ]]; then
                pkg_with_ver="${BASH_REMATCH[1]}"
                pkgrel="${BASH_REMATCH[2]}"
                
                # 再匹配一次: 最后一个 - 后面是 pkgver
                # 但要特殊处理 epoch，格式是 数字--版本
                if [[ "$pkg_with_ver" =~ ^(.+)-([0-9]+)--(.+)$ ]]; then
                    # 有 epoch 的情况: packagename-epoch--pkgver
                    pkg_name="${BASH_REMATCH[1]}"
                    epoch="${BASH_REMATCH[2]}"
                    base_ver="${BASH_REMATCH[3]}"
                    # 将 epoch- 转换回 epoch:
                    pkgver="${epoch}:${base_ver}"
                    pkg_ver="${pkgver}-${pkgrel}"
                    # 直接覆盖（packages.json 中后出现的就是更新的）
                    OLD_VERSIONS[$pkg_name]="$pkg_ver"
                elif [[ "$pkg_with_ver" =~ ^(.+)-(.+)$ ]]; then
                    # 无 epoch 的情况
                    pkg_name="${BASH_REMATCH[1]}"
                    pkgver="${BASH_REMATCH[2]}"
                    pkg_ver="${pkgver}-${pkgrel}"
                    # 直接覆盖（packages.json 中后出现的就是更新的）
                    OLD_VERSIONS[$pkg_name]="$pkg_ver"
                fi
            fi
        fi
    done < <(jq -r '.packages[]' old-packages.json)
    
    # 输出最终的旧版本列表
    for pkg_name in "${!OLD_VERSIONS[@]}"; do
        echo "  旧: $pkg_name = ${OLD_VERSIONS[$pkg_name]}"
    done | sort
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
    echo "==> 增量检测 AUR 包（批量+并行）"
    
    # 1. 批量获取所有包信息（一次 API 请求）
    echo "  → 批量获取 AUR 包信息"
    pkg_list=$(grep -v '^#' aur.txt | grep -v '^$' | sed 's/^/arg[]=/g' | tr '\n' '&' | sed 's/&$//')
    AUR_BULK_INFO=$(curl -s "https://aur.archlinux.org/rpc/v5/info?${pkg_list}")
    
    # 2. 创建临时目录
    temp_dir=$(mktemp -d)
    echo "$AUR_BULK_INFO" > "$temp_dir/bulk_info.json"
    
    # 保存旧版本信息供子进程读取
    for pkg in "${!OLD_VERSIONS[@]}"; do
        echo "${pkg}=${OLD_VERSIONS[$pkg]}" >> "$temp_dir/old_versions.txt"
    done
    
    # 3. 定义公共函数：计算 pkgver() 的真实版本
    compute_pkgver() {
        local pkgbuild_dir="$1"
        local pkg_name
        pkg_name=$(basename "$pkgbuild_dir")
        
        if [ ! -f "$pkgbuild_dir/PKGBUILD" ]; then
            echo "    ⚠ PKGBUILD 不存在" >&2
            return 1
        fi
        
        if ! command -v makepkg &>/dev/null; then
            echo "    ⚠ makepkg 命令不可用" >&2
            return 1
        fi
        
        # Save original pkgver and pkgrel before makepkg
        local original_pkgver original_pkgrel original_epoch
        original_pkgver=$(grep '^pkgver=' "$pkgbuild_dir/PKGBUILD" | cut -d= -f2)
        original_pkgrel=$(grep '^pkgrel=' "$pkgbuild_dir/PKGBUILD" | cut -d= -f2)
        original_epoch=$(grep '^epoch=' "$pkgbuild_dir/PKGBUILD" | cut -d= -f2 2>/dev/null || echo "")
        
        echo "    [makepkg] Original: pkgver=$original_pkgver, pkgrel=$original_pkgrel" >&2
        echo "    [makepkg] 执行 makepkg --nobuild..." >&2
        local makepkg_err
        makepkg_err=$(mktemp)
        if (cd "$pkgbuild_dir" && makepkg --nobuild --nodeps --skipinteg 2>"$makepkg_err" >/dev/null); then
            rm -f "$makepkg_err"
            
            # Read new pkgver after makepkg executed pkgver()
            # Use source to get expanded variable values (handles pkgver="$_tag" etc)
            local new_pkgver new_pkgrel
            new_pkgver=$(cd "$pkgbuild_dir" && bash -c 'source PKGBUILD 2>/dev/null && echo "$pkgver"')
            new_pkgrel=$(cd "$pkgbuild_dir" && bash -c 'source PKGBUILD 2>/dev/null && echo "$pkgrel"')
            
            echo "    [makepkg] After makepkg: pkgver=$new_pkgver, pkgrel=$new_pkgrel" >&2
            
            # Check if pkgver is valid (not a variable or placeholder)
            if [[ "$new_pkgver" =~ [\$\"\'] ]] || [ -z "$new_pkgver" ]; then
                echo "    [错误] pkgver() 执行失败，得到无效值: $new_pkgver" >&2
                return 1
            fi
            
            # Smart pkgrel handling:
            # If pkgver changed -> use new pkgrel (usually reset to 1)
            # If pkgver unchanged -> keep original pkgrel (PKGBUILD modifications need higher pkgrel)
            local final_pkgver final_pkgrel
            final_pkgver="$new_pkgver"
            
            if [ "$original_pkgver" = "$new_pkgver" ]; then
                # pkgver unchanged, preserve original pkgrel
                final_pkgrel="$original_pkgrel"
                echo "    [makepkg] pkgver unchanged, preserving original pkgrel=$original_pkgrel" >&2
            else
                # pkgver changed, use new pkgrel (reset to 1)
                final_pkgrel="$new_pkgrel"
                echo "    [makepkg] pkgver changed ($original_pkgver -> $new_pkgver), using new pkgrel=$new_pkgrel" >&2
            fi
            
            # Build final version string
            local result
            if [ -n "$original_epoch" ]; then
                result="${original_epoch}:${final_pkgver}-${final_pkgrel}"
            else
                result="${final_pkgver}-${final_pkgrel}"
            fi
            
            echo "    [makepkg] 成功计算版本: $result" >&2
            echo "$result"
            return 0
        else
            echo "    [错误] makepkg 执行失败" >&2
            # 显示错误信息（只显示 ERROR 行）
            grep "^==> ERROR:" "$makepkg_err" | head -3 | sed 's/^/    /' >&2
            rm -f "$makepkg_err"
        fi
        
        return 1
    }
    
    export -f compute_pkgver
    
    # 4. 下载 AUR PKGBUILD（优先 git clone）
    download_and_extract_pkgbuild() {
        local url_path="$1"
        local output_dir="$2"
        local pkg_name="$3"
        
        # 方案1: 优先使用 git clone（更可靠）
        if git clone --depth 1 "https://aur.archlinux.org/${pkg_name}.git" "$output_dir/$pkg_name" >/dev/null 2>&1; then
            return 0
        fi
        
        # 方案2: fallback 到 snapshot（如果 git clone 失败）
        local tmpfile
        tmpfile=$(mktemp)
        
        if ! curl -sfL "https://aur.archlinux.org${url_path}" -o "$tmpfile" 2>/dev/null; then
            rm -f "$tmpfile"
            return 1
        fi
        
        local file_size
        file_size=$(stat -f%z "$tmpfile" 2>/dev/null || stat -c%s "$tmpfile" 2>/dev/null || echo 0)
        
        # 检查文件大小（太小可能是错误页面）
        if [ "$file_size" -lt 500 ]; then
            rm -f "$tmpfile"
            return 1
        fi
        
        # 解压
        if ! tar -xz -C "$output_dir" -f "$tmpfile" 2>/dev/null; then
            rm -f "$tmpfile"
            return 1
        fi
        
        rm -f "$tmpfile"
        return 0
    }
    
    export -f download_and_extract_pkgbuild
    
    # 5. 定义并行检查函数
    check_aur_package_parallel() {
        local pkg_name="$1"
        local temp_dir="$2"
        
        # 检查是否在强制重建列表中
        if [ -f "$FORCE_REBUILD_LIST" ] && grep -qx "$pkg_name" "$FORCE_REBUILD_LIST"; then
            echo "  ⚡ $pkg_name: 强制重建"
            echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            return
        fi
        
        # 从批量结果中提取该包的信息
        pkg_info=$(jq -r ".results[] | select(.Name == \"$pkg_name\")" "$temp_dir/bulk_info.json")
        
        if [ -z "$pkg_info" ]; then
            echo "  ✗ $pkg_name: 在 AUR 未找到"
            return
        fi
        
        # 尝试检测 pkgver() 函数
        url_path=$(echo "$pkg_info" | jq -r '.URLPath')
        if [ -z "$url_path" ] || [ "$url_path" = "null" ]; then
            # 没有 URLPath，直接进行版本对比
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ]; then
                echo "  ✓ $pkg_name: 新包 ($current_ver)，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            elif [ "$current_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化 $old_ver → $current_ver，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
            return
        fi
        
        # 下载并解压 PKGBUILD
        local tmpdir
        tmpdir=$(mktemp -d)
        
        if ! download_and_extract_pkgbuild "$url_path" "$tmpdir" "$pkg_name"; then
            rm -rf "$tmpdir"
            # 下载失败，进行普通版本对比
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ] || [ "$current_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化（下载 PKGBUILD 失败），需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
            return
        fi
        
        # 查找 PKGBUILD
        local pkgbuild_file
        pkgbuild_file=$(find "$tmpdir" -name PKGBUILD -type f | head -1)
        
        if [ -z "$pkgbuild_file" ] || [ ! -f "$pkgbuild_file" ]; then
            rm -rf "$tmpdir"
            echo "  ⚠ $pkg_name: 未找到 PKGBUILD，使用 AUR 版本对比" >&2
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ] || [ "$current_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            fi
            return
        fi
        
        # 检查是否有 pkgver() 函数
        if ! grep -qE '^\s*pkgver\s*\(\)' "$pkgbuild_file"; then
            rm -rf "$tmpdir"
            # 没有 pkgver()，使用 AUR 版本对比
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ] || [ "$current_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化 $old_ver → $current_ver，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
            return
        fi
        
        # 有 pkgver()，计算真实版本
        echo "  → $pkg_name: 检测到 pkgver() 函数，计算真实版本..."
        local pkgbuild_dir real_ver
        pkgbuild_dir=$(dirname "$pkgbuild_file")
        real_ver=$(compute_pkgver "$pkgbuild_dir")
        rm -rf "$tmpdir"
        
        if [ -n "$real_ver" ]; then
            # 成功获取真实版本
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ]; then
                echo "  ✓ $pkg_name: 新包 ($real_ver)，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            elif [ "$real_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化 $old_ver → $real_ver，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            else
                echo "  → $pkg_name: 版本未变化 ($real_ver)"
            fi
        else
            # 无法计算真实版本，保守构建
            echo "  ✓ $pkg_name: 无法计算版本，保守构建"
            echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
        fi
    }
    
    export -f check_aur_package_parallel
    
    # 6. 并行检查（5 个并发，因为 makepkg --nobuild 会下载源码）
    echo "  → 并行检查包更新（5 并发）"
    grep -v '^#' aur.txt | grep -v '^$' | \
        xargs -I {} -P 5 bash -c 'check_aur_package_parallel "$@"' _ {} "$temp_dir"
    
    # 7. 收集结果
    cat "$temp_dir"/*.build 2>/dev/null > "$AUR_OUTPUT_FILE" || true
    
    # 清理临时目录
    rm -rf "$temp_dir"
fi

# 检查 pinned 包（始终构建）
if [ -f "aur-pinned.txt" ]; then
    echo ""
    echo "==> 检查固定版本包"
    while IFS='=' read -r pkg_name _; do
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
    echo "==> 增量检测本地包（并行）"
    
    # 创建临时目录
    temp_dir=$(mktemp -d)
    
    # 保存旧版本信息供子进程读取
    for pkg in "${!OLD_VERSIONS[@]}"; do
        echo "${pkg}=${OLD_VERSIONS[$pkg]}" >> "$temp_dir/old_versions.txt"
    done
    
    # 定义并行检查函数
    check_local_package_parallel() {
        local pkg_dir="$1"
        local temp_dir="$2"
        local pkg_name
        pkg_name=$(basename "$pkg_dir")
        
        if [ ! -f "$pkg_dir/PKGBUILD" ]; then
            return
        fi
        
        # 检查是否在强制重建列表中
        if [ -f "$FORCE_REBUILD_LIST" ] && grep -qx "$pkg_name" "$FORCE_REBUILD_LIST"; then
            echo "  ⚡ $pkg_name: 强制重建" >&2
            echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            return
        fi
        
        log_debug "$pkg_name: Checking at $pkg_dir"
        log_debug "$pkg_name: pkgrel in file = $(grep '^pkgrel=' "$pkg_dir/PKGBUILD" 2>/dev/null)"
        
        # 检查是否有 pkgver() 函数
        if grep -qE '^\s*pkgver\s*\(\)' "$pkg_dir/PKGBUILD"; then
            echo "  → $pkg_name: 检测到 pkgver() 函数，计算真实版本..."
            
            # 先直接读取 PKGBUILD 中的静态值
            local static_pkgrel static_pkgver
            static_pkgrel=$(grep '^pkgrel=' "$pkg_dir/PKGBUILD" | cut -d= -f2)
            static_pkgver=$(grep '^pkgver=' "$pkg_dir/PKGBUILD" | cut -d= -f2)
            log_debug "$pkg_name: Static values - pkgver=$static_pkgver, pkgrel=$static_pkgrel"
            
            local real_ver
            real_ver=$(compute_pkgver "$pkg_dir")
            log_debug "$pkg_name: compute_pkgver returned: '$real_ver'"
            
            if [ -n "$real_ver" ]; then
                # 成功获取真实版本，进行对比
                old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
                log_debug "$pkg_name: Old version from latest release: '$old_ver'"
                log_debug "$pkg_name: New computed version: '$real_ver'"
                
                if [ -z "$old_ver" ]; then
                    echo "  ✓ $pkg_name: 新包 ($real_ver)，需要构建"
                    echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                elif [ "$real_ver" != "$old_ver" ]; then
                    echo "  ✓ $pkg_name: 版本变化 $old_ver → $real_ver，需要构建"
                    echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                else
                    echo "  → $pkg_name: 版本未变化 ($real_ver)"
                fi
            else
                # 无法获取真实版本，保守构建
                echo "  ✓ $pkg_name: 无法计算版本，保守构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            fi
            return
        fi
        
        # 提取 PKGBUILD 中的版本
        (
            source "$pkg_dir/PKGBUILD" 2>/dev/null || exit 1
            current_ver="${pkgver}-${pkgrel}"
            
            # 对比版本
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ]; then
                echo "  ✓ $pkg_name: 新包 ($current_ver)，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            elif [ "$current_ver" != "$old_ver" ]; then
                echo "  ✓ $pkg_name: 版本变化 $old_ver → $current_ver，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
        )
    }
    
    export -f check_local_package_parallel
    
    # 并行检查（5 个并发，本地包数量较少）
    find local -mindepth 1 -maxdepth 1 -type d -print0 | \
        xargs -0 -I {} -P 5 bash -c 'check_local_package_parallel "$@"' _ {} "$temp_dir"
    
    # 收集结果
    cat "$temp_dir"/*.build 2>/dev/null > "$LOCAL_OUTPUT_FILE" || true
    
    # 清理临时目录
    rm -rf "$temp_dir"
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

# 清理临时文件
rm -f "$FORCE_REBUILD_LIST"

