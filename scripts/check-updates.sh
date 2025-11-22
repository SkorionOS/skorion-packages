#!/bin/bash
# 检测 AUR 包和本地包更新
# 对比当前版本与 latest Release 中的版本
#
# 环境变量:
#   REPO_OWNER          - GitHub 仓库所有者
#   REPO_NAME           - 仓库名称
#   FORCE_REBUILD       - 强制重建的包列表(逗号分隔)
#   LOG_LEVEL           - 日志级别: DEBUG, INFO(默认), WARN, ERROR
#   PARALLEL_JOBS       - 并行检查的包数量
#
# 使用示例:
#   LOG_LEVEL=DEBUG bash scripts/check-updates.sh
#   FORCE_REBUILD=package1,package2 bash scripts/check-updates.sh
#   PARALLEL_JOBS=10 bash scripts/check-updates.sh

set -e

REPO_OWNER="${REPO_OWNER:-SkorionOS}"
REPO_NAME="${REPO_NAME:-skorion-packages}"
AUR_OUTPUT_FILE="${AUR_OUTPUT_FILE:-updated-aur-packages.txt}"
LOCAL_OUTPUT_FILE="${LOCAL_OUTPUT_FILE:-updated-local-packages.txt}"
FORCE_REBUILD="${FORCE_REBUILD:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"  # DEBUG, INFO, WARN, ERROR
export LOG_LEVEL  # 导出供子进程使用

# 并发检查配置
PARALLEL_JOBS="${PARALLEL_JOBS:-8}"  # 并行检查包的数量

# ==============================================================================
# 工具函数
# ==============================================================================
# 时间戳函数
timestamp() {
    date '+%H:%M:%S'
}
export -f timestamp

# 版本号标准化函数 - 将 git hash 统一截断为 7 位，避免长度波动导致误判
normalize_version_for_comparison() {
    local version="$1"
    # 将版本号中 8-12 位的十六进制字符串截断为 7 位
    # 例如: 7c193ffef1a8 -> 7c193ff
    echo "$version" | sed -E 's/\b([0-9a-f]{7})[0-9a-f]{1,5}\b/\1/g'
}
export -f normalize_version_for_comparison

# ==============================================================================
# 日志函数
# ==============================================================================
log_debug() {
    [[ "$LOG_LEVEL" == "DEBUG" ]] && echo "  [DEBUG] $*" >&2
    return 0
}

log_info() {
    [[ "$LOG_LEVEL" =~ ^(DEBUG|INFO)$ ]] && echo "  $*" >&2
    return 0
}

log_warn() {
    [[ "$LOG_LEVEL" =~ ^(DEBUG|INFO|WARN)$ ]] && echo "  ⚠ $*" >&2
    return 0
}

log_error() {
    echo "  ✗ $*" >&2
    return 0
}

log_success() {
    echo "  ✓ $*" >&2
    return 0
}

log_header() {
    echo "==> $*"
    return 0
}

export -f log_debug log_info log_warn log_error log_success log_header

log_header "检测包更新"

# 处理强制重建的包列表
FORCE_REBUILD_LIST=$(mktemp)
if [ -n "$FORCE_REBUILD" ]; then
    log_info "==> 强制重建包列表: $FORCE_REBUILD"
    IFS=',' read -ra FORCE_PACKAGES <<< "$FORCE_REBUILD"
    for pkg in "${FORCE_PACKAGES[@]}"; do
        pkg=$(echo "$pkg" | xargs)  # 去除空格
        if [ -n "$pkg" ]; then
            echo "$pkg" >> "$FORCE_REBUILD_LIST"
            log_info "  - $pkg (强制重建)"
        fi
    done
    log_debug "Force rebuild list file: $FORCE_REBUILD_LIST"
    log_debug "Force rebuild list contents:"
    log_debug "$(cat "$FORCE_REBUILD_LIST")"
fi
export FORCE_REBUILD_LIST

# 下载 latest Release 的包文件列表（从 assets）
echo "==> 下载 latest Release 信息"
MAX_RETRIES=5
RETRY_COUNT=0
LATEST_JSON=""

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
  LATEST_JSON=$(curl -s "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/latest" 2>/dev/null)
  
  # 检查是否成功获取到有效的 JSON
  if [ -n "$LATEST_JSON" ] && echo "$LATEST_JSON" | jq -e . >/dev/null 2>&1; then
    log_success "下载 Release 信息成功"
    break
  else
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
      WAIT_TIME=$((2 ** RETRY_COUNT))  # Exponential backoff: 2, 4, 8, 16, 32 seconds
      log_warn "下载 Release 信息失败，${WAIT_TIME}秒后重试 ($RETRY_COUNT/$MAX_RETRIES)..."
      sleep $WAIT_TIME
    else
      log_warn "下载 Release 信息失败，达到最大重试次数 ($MAX_RETRIES)，将视为首次构建"
      LATEST_JSON="{}"
    fi
  fi
done

# 检查是否是首次构建
FIRST_BUILD=false
if echo "$LATEST_JSON" | jq -e '.message == "Not Found"' > /dev/null 2>&1; then
    echo "==> 首次构建（未找到 latest Release）"
    FIRST_BUILD=true
fi

# 从 Release Assets 获取已发布的包版本
declare -A OLD_VERSIONS

if [ "$FIRST_BUILD" = false ]; then
    echo "==> 从 Release Assets 解析包版本"
    
    # 获取所有 .pkg.tar.zst 文件名
    ASSET_PACKAGES=$(echo "$LATEST_JSON" | jq -r '.assets[]? | select(.name | endswith(".pkg.tar.zst")) | .name' 2>/dev/null || echo "")
    
    if [ -z "$ASSET_PACKAGES" ]; then
        echo "==> 警告: Release 中没有包文件"
        FIRST_BUILD=true
    else
        # 计数器和总数
        pkg_count=0
        total_assets=$(echo "$ASSET_PACKAGES" | wc -l | tr -d ' ')
        echo "  → [$(timestamp)] 共 $total_assets 个文件需要解析..."
        
        while IFS= read -r filename; do
            if [ -z "$filename" ]; then
                continue
            fi
            
            # URL 解码文件名（%2B -> +, %20 -> 空格等）
            filename=$(python3 -c "import sys, urllib.parse; print(urllib.parse.unquote(sys.stdin.read().strip()))" <<< "$filename")
            
            # 使用 +=1 避免 set -e 下 ((pkg_count++)) 在初始值为 0 时返回 1
            ((pkg_count += 1))
            
            # 每 30 个包输出一次进度
            if (( pkg_count % 30 == 0 )) || (( pkg_count == total_assets )); then
                echo "  → [$(timestamp)] 解析进度: $pkg_count/$total_assets"
            fi
            
            # 移除 .pkg.tar.zst 后缀
            pkg_full="${filename%.pkg.tar.zst}"
            
            # 解析格式: pkgname-pkgver-pkgrel-arch
            # 支持 epoch，文件名中 epoch 用 -- 表示（因为文件系统不允许 :）
            # 例如: mesa-1--25.2.6-1-x86_64 表示 mesa 1:25.2.6-1
            
            # 从右往左提取 arch 和 pkgrel
            if [[ "$pkg_full" =~ ^(.+)-([^-]+)-([^-]+)$ ]]; then
                pkg_with_ver="${BASH_REMATCH[1]}"  # packagename-[epoch--]pkgver
                pkgrel="${BASH_REMATCH[2]}"
                arch="${BASH_REMATCH[3]}"
                
                # 检查是否有 epoch（格式：packagename-epoch--pkgver）
                if [[ "$pkg_with_ver" =~ ^(.+)-([0-9]+)--(.+)$ ]]; then
                    # 有 epoch
                    pkg_name="${BASH_REMATCH[1]}"
                    epoch="${BASH_REMATCH[2]}"
                    pkgver="${BASH_REMATCH[3]}"
                    full_ver="${epoch}:${pkgver}-${pkgrel}"
                elif [[ "$pkg_with_ver" =~ ^(.+)-(.+)$ ]]; then
                    # 无 epoch
                    pkg_name="${BASH_REMATCH[1]}"
                    pkgver="${BASH_REMATCH[2]}"
                    full_ver="${pkgver}-${pkgrel}"
                else
                    # 无法解析，跳过
                    continue
                fi
                
                # 存储版本（如果有重复包名，比较版本号保留最新的）
                if [ -n "${OLD_VERSIONS[$pkg_name]:-}" ]; then
                    # 已经有这个包了，比较版本号
                    old_ver="${OLD_VERSIONS[$pkg_name]}"
                    
                    # 使用 vercmp 或 sort -V 比较版本
                    if command -v vercmp > /dev/null 2>&1; then
                        # 使用 pacman 的 vercmp（最准确）
                        cmp_result=$(vercmp "$full_ver" "$old_ver")
                        if [ "$cmp_result" = "1" ]; then
                            # 新版本更新
                            OLD_VERSIONS[$pkg_name]="$full_ver"
                            echo "  ⚠ 检测到 $pkg_name 有多个版本，保留较新的: $full_ver (vs $old_ver)"
                        fi
                    else
                        # fallback 到 sort -V
                        newer=$(printf "%s\n%s\n" "$old_ver" "$full_ver" | sort -V -r | head -1)
                        if [ "$newer" = "$full_ver" ]; then
                            OLD_VERSIONS[$pkg_name]="$full_ver"
                            echo "  ⚠ 检测到 $pkg_name 有多个版本，保留较新的: $full_ver (vs $old_ver)"
                        fi
                    fi
                else
                    # 首次遇到这个包
                    OLD_VERSIONS[$pkg_name]="$full_ver"
                fi
            fi
        done <<< "$ASSET_PACKAGES"
        
        # 输出解析结果
        echo ""
        echo "==> [$(timestamp)] 已解析 ${#OLD_VERSIONS[@]} 个包的版本"
        echo ""
        
        # 显示所有包的版本列表
        for pkg_name in "${!OLD_VERSIONS[@]}"; do
            echo "  旧: $pkg_name = ${OLD_VERSIONS[$pkg_name]}"
        done | sort
    fi
fi

# ==============================================================================
# 检测 AUR 包更新
# ==============================================================================
echo ""
echo "==> 检查 AUR 包"
: > "$AUR_OUTPUT_FILE"

if [ "$FIRST_BUILD" = true ]; then
    echo "==> 首次构建，构建所有 AUR 包"
    grep -v '^#' aur.conf | grep -v '^$' > "$AUR_OUTPUT_FILE"
else
    echo "==> 增量检测 AUR 包（批量+并行）"
    
    # 1. 批量获取所有包信息（一次 API 请求）
    pkg_count=$(grep -v '^#' aur.conf | grep -v '^$' | wc -l | tr -d ' ')
    echo "  → [$(timestamp)] 批量获取 $pkg_count 个 AUR 包信息..."
    pkg_list=$(grep -v '^#' aur.conf | grep -v '^$' | sed 's/^/arg[]=/g' | tr '\n' '&' | sed 's/&$//')
    
    MAX_RETRIES=5
    RETRY_COUNT=0
    AUR_BULK_INFO=""
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
      AUR_BULK_INFO=$(curl -s "https://aur.archlinux.org/rpc/v5/info?${pkg_list}" 2>/dev/null)
      
      # 检查是否成功获取到有效的 JSON
      if [ -n "$AUR_BULK_INFO" ] && echo "$AUR_BULK_INFO" | jq -e '.results' >/dev/null 2>&1; then
        log_info "  ✓ [$(timestamp)] 批量获取 AUR 信息成功"
        break
      else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
          WAIT_TIME=$((2 ** RETRY_COUNT))
          log_warn "  ⚠ 批量获取 AUR 信息失败，${WAIT_TIME}秒后重试 ($RETRY_COUNT/$MAX_RETRIES)..."
          sleep $WAIT_TIME
        else
          log_error "  ✗ 批量获取 AUR 信息失败，达到最大重试次数"
          exit 1
        fi
      fi
    done
    
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
        # Use source to get expanded values (handles pkgver="$_tag" etc)
        local original_pkgver original_pkgrel original_epoch
        eval "$(cd "$pkgbuild_dir" && unset epoch pkgver pkgrel && source PKGBUILD 2>/dev/null && echo "original_pkgver='$pkgver'; original_pkgrel='$pkgrel'; original_epoch='${epoch:-}'")"
        
        echo "    [makepkg] Original (expanded): pkgver=$original_pkgver, pkgrel=$original_pkgrel" >&2
        echo "    [makepkg][$(timestamp)] 开始执行 makepkg --nobuild..." >&2
        # echo "    [makepkg] 注意：如果包含 pkgver() 函数且需要下载源码，可能需要较长时间" >&2
        local makepkg_err
        makepkg_err=$(mktemp)
        local makepkg_start
        makepkg_start=$(date +%s)
        
        # 后台执行 makepkg，同时显示心跳
        (cd "$pkgbuild_dir" && makepkg --nobuild --nodeps --skipinteg 2>"$makepkg_err" >/dev/null) &
        local makepkg_pid=$!
        
        # 显示心跳，每 10 秒输出一次
        local heartbeat_count=0
        while kill -0 $makepkg_pid 2>/dev/null; do
            sleep 10
            ((heartbeat_count += 10))
            echo "    [makepkg][$(timestamp)] $pkg_name: 仍在运行... (已耗时 ${heartbeat_count}s)" >&2
        done
        
        # 等待 makepkg 完成并获取退出状态
        wait $makepkg_pid
        local makepkg_status=$?
        
        local makepkg_end
        makepkg_end=$(date +%s)
        local makepkg_time=$((makepkg_end - makepkg_start))
        
        if [ $makepkg_status -eq 0 ]; then
            echo "    [makepkg][$(timestamp)] 完成 (总耗时 ${makepkg_time}s)" >&2
            rm -f "$makepkg_err"
            
            # Read new pkgver after makepkg executed pkgver()
            # Use source to get expanded variable values (handles pkgver="$_tag" etc)
            local new_pkgver new_pkgrel
            new_pkgver=$(cd "$pkgbuild_dir" && bash -c 'unset epoch pkgver pkgrel && source PKGBUILD 2>/dev/null && echo "$pkgver"')
            new_pkgrel=$(cd "$pkgbuild_dir" && bash -c 'unset epoch pkgver pkgrel && source PKGBUILD 2>/dev/null && echo "$pkgrel"')
            
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
            echo "    [错误][$(timestamp)] makepkg 执行失败 (总耗时 ${makepkg_time}s)" >&2
            # 显示错误信息（只显示 ERROR 行）
            grep "^==> ERROR:" "$makepkg_err" | head -3 | sed 's/^/    /' >&2
            rm -f "$makepkg_err"
        fi
        
        return 1
    }
    
    export -f compute_pkgver
    
    # 4. 统一的版本检测和比较函数
    check_package_version() {
        local pkgbuild_dir="$1"   # PKGBUILD 所在目录
        local pkg_name="$2"       # 包名
        local temp_dir="$3"       # 临时目录
        
        if [ ! -f "$pkgbuild_dir/PKGBUILD" ]; then
            echo "  ⚠ $pkg_name: PKGBUILD 不存在" >&2
            return 1
        fi
        
        local current_ver
        
        # 1. 获取当前版本
        if grep -qE '^\s*pkgver\s*\(\)' "$pkgbuild_dir/PKGBUILD"; then
            # 有 pkgver() 函数，计算真实版本
            echo "  → $pkg_name: 检测到 pkgver() 函数，计算真实版本..."
            current_ver=$(compute_pkgver "$pkgbuild_dir")
            
            if [ -z "$current_ver" ]; then
                echo "  ✓ $pkg_name: 无法计算版本，保守构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                return 0
            fi
        else
            # 无 pkgver() 函数，直接从 PKGBUILD 提取（包含 epoch）
            current_ver=$(
                cd "$pkgbuild_dir" || exit 1
                # 清除可能继承自父 shell 的变量，防止污染
                unset epoch pkgver pkgrel
                source PKGBUILD 2>/dev/null || exit 1
                if [ -n "${epoch:-}" ]; then
                    echo "${epoch}:${pkgver}-${pkgrel}"
                else
                    echo "${pkgver}-${pkgrel}"
                fi
            )
            
            if [ -z "$current_ver" ]; then
                echo "  ⚠ $pkg_name: 无法提取版本" >&2
                return 1
            fi
        fi
        
        # 2. 比较版本并决定是否构建
        local old_ver
        old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
        
        if [ -z "$old_ver" ]; then
            echo "  ✓ $pkg_name: 新包 ($current_ver)，需要构建"
            echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
        elif [ "$current_ver" != "$old_ver" ]; then
            # 智能比对：检查是否只是 git hash 长度差异
            local old_normalized new_normalized
            old_normalized=$(normalize_version_for_comparison "$old_ver")
            new_normalized=$(normalize_version_for_comparison "$current_ver")
            
            if [ "$old_normalized" = "$new_normalized" ]; then
                echo "  → $pkg_name: 版本未变化 ($current_ver，已忽略 git hash 长度差异)"
            else
                echo "  ✓ $pkg_name: 版本变化 $old_ver → $current_ver，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            fi
        else
            echo "  → $pkg_name: 版本未变化 ($current_ver)"
        fi
        
        return 0
    }
    
    export -f check_package_version
    
    # 5. 下载 AUR PKGBUILD（优先 git clone）
    download_and_extract_pkgbuild() {
        local url_path="$1"
        local output_dir="$2"
        local pkg_name="$3"
        local git_hash="${4:-}"  # 可选：指定 commit hash
        
        # 方案1: 优先使用 git clone（更可靠）
        log_debug "[$(timestamp)] $pkg_name: 开始 git clone PKGBUILD..."
        if git clone --depth 1 "https://aur.archlinux.org/${pkg_name}.git" "$output_dir/$pkg_name" >/dev/null 2>&1; then
            log_debug "[$(timestamp)] $pkg_name: git clone 完成"
            # 如果指定了 commit，切换到该 commit
            if [ -n "$git_hash" ]; then
                log_debug "[$(timestamp)] $pkg_name: 切换到指定 commit ${git_hash:0:7}..."
                if (cd "$output_dir/$pkg_name" && git fetch --depth 50 origin "$git_hash" >/dev/null 2>&1 && git checkout "$git_hash" >/dev/null 2>&1); then
                    log_debug "[$(timestamp)] $pkg_name: checkout 完成"
                    return 0
                else
                    # checkout 失败，清理并 fallback
                    log_debug "[$(timestamp)] $pkg_name: checkout 失败，清理..."
                    rm -rf "${output_dir:?}/${pkg_name:?}"
                    return 1
                fi
            fi
            return 0
        fi
        
        log_debug "[$(timestamp)] $pkg_name: git clone 失败，尝试 snapshot..."
        
        # 如果指定了 git_hash，不使用 snapshot fallback（snapshot 不支持指定 commit）
        if [ -n "$git_hash" ]; then
            return 1
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
        
        echo "[$(timestamp)] ⏳ 开始检查: $pkg_name" >&2
        
        # 跳过 pinned 包（由 pinned 逻辑单独处理）
        if [ -f "aur-pinned.conf" ] && grep -q "^${pkg_name}=" "aur-pinned.conf" 2>/dev/null; then
            echo "[$(timestamp)] → $pkg_name: pinned 包，跳过 AUR 版本检查" >&2
            return
        fi
        
        # 检查是否在强制重建列表中
        log_debug "  [force-check] Checking if $pkg_name is in force rebuild list: $FORCE_REBUILD_LIST"
        if [ -f "$FORCE_REBUILD_LIST" ] && [ -s "$FORCE_REBUILD_LIST" ]; then
            log_debug "  [force-check] Force rebuild list exists, contents:"
            log_debug "$(cat "$FORCE_REBUILD_LIST" 2>/dev/null || echo '  [empty or unreadable]')"
            if grep -Fxq "$pkg_name" "$FORCE_REBUILD_LIST" 2>/dev/null; then
                log_info "  ⚡ $pkg_name: 强制重建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                return
            fi
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
                # 智能比对：检查是否只是 git hash 长度差异
                local old_normalized new_normalized
                old_normalized=$(normalize_version_for_comparison "$old_ver")
                new_normalized=$(normalize_version_for_comparison "$current_ver")
                
                if [ "$old_normalized" = "$new_normalized" ]; then
                    echo "  → $pkg_name: 版本未变化 ($current_ver，已忽略 git hash 长度差异)"
                else
                    echo "  ✓ $pkg_name: 版本变化 $old_ver → $current_ver，需要构建"
                    echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                fi
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
            # 下载失败，使用 AUR API 版本进行比对
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ]; then
                echo "  ✓ $pkg_name: 新包 ($current_ver)，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            elif [ "$current_ver" != "$old_ver" ]; then
                # 智能比对：检查是否只是 git hash 长度差异
                local old_normalized new_normalized
                old_normalized=$(normalize_version_for_comparison "$old_ver")
                new_normalized=$(normalize_version_for_comparison "$current_ver")
                
                if [ "$old_normalized" = "$new_normalized" ]; then
                    echo "  → $pkg_name: 版本未变化 ($current_ver，已忽略 git hash 长度差异)"
                else
                    echo "  ✓ $pkg_name: 版本变化（下载失败，使用 AUR 版本）$old_ver → $current_ver，需要构建"
                    echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                fi
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
            return
        fi
        
        # 查找 PKGBUILD 目录
        local pkgbuild_dir
        pkgbuild_dir=$(find "$tmpdir" -name PKGBUILD -type f -exec dirname {} \; | head -1)
        
        if [ -z "$pkgbuild_dir" ] || [ ! -f "$pkgbuild_dir/PKGBUILD" ]; then
            rm -rf "$tmpdir"
            # 未找到 PKGBUILD，使用 AUR API 版本
            current_ver=$(echo "$pkg_info" | jq -r '.Version')
            old_ver=$(grep "^${pkg_name}=" "$temp_dir/old_versions.txt" 2>/dev/null | cut -d= -f2-)
            
            if [ -z "$old_ver" ]; then
                echo "  ✓ $pkg_name: 新包 ($current_ver)，需要构建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
            elif [ "$current_ver" != "$old_ver" ]; then
                # 智能比对：检查是否只是 git hash 长度差异
                local old_normalized new_normalized
                old_normalized=$(normalize_version_for_comparison "$old_ver")
                new_normalized=$(normalize_version_for_comparison "$current_ver")
                
                if [ "$old_normalized" = "$new_normalized" ]; then
                    echo "  → $pkg_name: 版本未变化 ($current_ver，已忽略 git hash 长度差异)"
                else
                    echo "  ✓ $pkg_name: 版本变化（未找到 PKGBUILD）$old_ver → $current_ver，需要构建"
                    echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                fi
            else
                echo "  → $pkg_name: 版本未变化 ($current_ver)"
            fi
            return
        fi
        
        # 使用统一的版本检测函数
        check_package_version "$pkgbuild_dir" "$pkg_name" "$temp_dir"
        rm -rf "$tmpdir"
        
        echo "[$(timestamp)] ✓ 完成检查: $pkg_name" >&2
    }
    
    export -f check_aur_package_parallel
    
    # 6. 并行检查（因为 makepkg --nobuild 会下载源码）
    echo ""
    echo "  → [$(timestamp)] 开始并行检查（$PARALLEL_JOBS 并发，共 $pkg_count 个包）"
    echo "  → 提示：包含 pkgver() 函数的包需要下载源码验证版本，会比较慢"
    echo "  → 预计耗时：快速包 1-3s，需要下载源码的包 5-60s"
    echo "  → 下方将实时显示每个包的检查进度..."
    echo ""
    
    check_start=$(date +%s)
    
    grep -v '^#' aur.conf | grep -v '^$' | \
        xargs -I {} -P "$PARALLEL_JOBS" bash -c 'check_aur_package_parallel "$@"' _ {} "$temp_dir"
    
    check_end=$(date +%s)
    check_time=$((check_end - check_start))
    echo ""
    echo "  → [$(timestamp)] AUR 包并行检查完成 (总耗时 ${check_time}s)"
    
    # 7. 收集结果
    cat "$temp_dir"/*.build 2>/dev/null > "$AUR_OUTPUT_FILE" || true
    
    # 清理临时目录
    rm -rf "$temp_dir"
fi

# ==============================================================================
# 检测 Pinned 包更新
# ==============================================================================

# 检查单个 pinned 包
check_pinned_package() {
    local pkg_name="$1"
    local git_hash="$2"
    local old_ver="$3"
    
    # 没有指定 commit：只在缺失时构建
    if [ -z "$git_hash" ]; then
        if [ -z "$old_ver" ]; then
            echo "  ✓ $pkg_name (pinned): 首次构建"
            return 0  # 需要构建
        else
            echo "  → $pkg_name (pinned): 已存在 ($old_ver)"
            return 1  # 不需要构建
        fi
    fi
    
    # 有指定 commit：检查版本
    echo "  → $pkg_name (pinned@${git_hash:0:7}): 检查版本..."
    
    # 下载 PKGBUILD
    local tmpdir
    tmpdir=$(mktemp -d)
    
    if ! download_and_extract_pkgbuild "" "$tmpdir" "$pkg_name" "$git_hash"; then
        echo "  ⚠ $pkg_name: 下载失败，保守构建"
        rm -rf "$tmpdir"
        return 0  # 需要构建
    fi
    
    # 查找 PKGBUILD 目录
    local pkgbuild_dir
    pkgbuild_dir=$(find "$tmpdir" -name PKGBUILD -type f -exec dirname {} \; | head -1)
    
    if [ -z "$pkgbuild_dir" ]; then
        echo "  ⚠ $pkg_name: 未找到 PKGBUILD，保守构建"
        rm -rf "$tmpdir"
        return 0  # 需要构建
    fi
    
    # 创建临时目录用于版本检测（适配 check_package_version 的接口）
    local temp_check_dir
    temp_check_dir=$(mktemp -d)
    
    # 准备旧版本信息
    if [ -n "$old_ver" ]; then
        echo "${pkg_name}=${old_ver}" > "$temp_check_dir/old_versions.txt"
    else
        touch "$temp_check_dir/old_versions.txt"
    fi
    
    # 使用统一的版本检测函数
    check_package_version "$pkgbuild_dir" "$pkg_name" "$temp_check_dir"
    
    # 检查是否需要构建
    local needs_build=1
    if [ -f "$temp_check_dir/${pkg_name}.build" ]; then
        needs_build=0
    fi
    
    # 清理
    rm -rf "$tmpdir" "$temp_check_dir"
    
    return $needs_build
}

# 检查 pinned 包
if [ -f "aur-pinned.conf" ]; then
    echo ""
    echo "==> 检查固定版本包"
    
    export -f check_pinned_package
    
    while IFS='=' read -r pkg_name git_hash; do
        # 跳过注释和空行
        [[ "$pkg_name" =~ ^#.*$ ]] && continue
        [ -z "$pkg_name" ] && continue
        
        # 获取旧版本
        old_ver=""
        if [ -n "${OLD_VERSIONS[$pkg_name]}" ]; then
            old_ver="${OLD_VERSIONS[$pkg_name]}"
        fi
        
        # 检查是否需要构建
        if check_pinned_package "$pkg_name" "$git_hash" "$old_ver"; then
            echo "$pkg_name" >> "$AUR_OUTPUT_FILE"
        fi
    done < aur-pinned.conf
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
    
    local_pkg_count=$(find local -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
    echo ""
    echo "  → [$(timestamp)] 开始检查本地包（共 $local_pkg_count 个）"
    echo "  → 提示：本地包通常包含 pkgver() 函数，需要验证版本"
    echo ""
    
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
        
        echo "[$(timestamp)] ⏳ 检查本地包: $pkg_name" >&2
        
        # 检查是否在强制重建列表中
        log_debug "  [force-check] Checking if $pkg_name is in force rebuild list: $FORCE_REBUILD_LIST"
        if [ -f "$FORCE_REBUILD_LIST" ] && [ -s "$FORCE_REBUILD_LIST" ]; then
            log_debug "  [force-check] Force rebuild list exists, contents:"
            log_debug "$(cat "$FORCE_REBUILD_LIST" 2>/dev/null || echo '  [empty or unreadable]')"
            if grep -Fxq "$pkg_name" "$FORCE_REBUILD_LIST" 2>/dev/null; then
                log_info "  ⚡ $pkg_name: 强制重建"
                echo "$pkg_name" > "$temp_dir/${pkg_name}.build"
                return
            fi
        fi
        
        log_debug "$pkg_name: Checking at $pkg_dir"
        log_debug "$pkg_name: pkgrel in file = $(grep '^pkgrel=' "$pkg_dir/PKGBUILD" 2>/dev/null)"
        
        # 使用统一的版本检测函数
        check_package_version "$pkg_dir" "$pkg_name" "$temp_dir"
        
        echo "[$(timestamp)] ✓ 完成本地包: $pkg_name" >&2
    }
    
    export -f check_local_package_parallel
    
    # 并行检查（本地包数量较少）
    local_check_start=$(date +%s)
    
    find local -mindepth 1 -maxdepth 1 -type d -print0 | \
        xargs -0 -I {} -P "$PARALLEL_JOBS" bash -c 'check_local_package_parallel "$@"' _ {} "$temp_dir"
    
    local_check_end=$(date +%s)
    local_check_time=$((local_check_end - local_check_start))
    
    echo ""
    echo "  → [$(timestamp)] 本地包检查完成 (总耗时 ${local_check_time}s)"
    
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
echo "==> [$(timestamp)] 检测完成"
echo "==> AUR 包: $AUR_COUNT 个需要构建"
if [ "$AUR_COUNT" -gt 0 ]; then
    echo "    构建列表:"
    cat "$AUR_OUTPUT_FILE" | sed 's/^/      - /'
fi
echo "==> 本地包: $LOCAL_COUNT 个需要构建"
if [ "$LOCAL_COUNT" -gt 0 ]; then
    echo "    构建列表:"
    cat "$LOCAL_OUTPUT_FILE" | sed 's/^/      - /'
fi
echo "========================================"

# 清理临时文件
rm -f "$FORCE_REBUILD_LIST"

