#!/bin/bash
# Generate pacman repository database from all packages

set -e

OUTPUT_DIR="${OUTPUT_DIR:-./output}"
REPO_NAME="skorion"

echo "==> 生成 pacman 仓库数据库"

cd "$OUTPUT_DIR"

# 统计包数量
PACKAGE_COUNT=$(ls -1 *.pkg.tar.zst 2>/dev/null | wc -l)
echo "==> 找到 $PACKAGE_COUNT 个包文件"

if [ "$PACKAGE_COUNT" -eq 0 ]; then
    echo "==> 警告: 没有找到包文件"
    # 检查是否存在数据库（可能是增量更新但没有新包）
    if [ ! -f "${REPO_NAME}.db.tar.gz" ]; then
        echo "==> 错误: 既没有包文件也没有数据库"
        exit 1
    fi
    echo "==> 保留现有数据库"
else
    # 检查是否存在旧数据库（增量更新模式）
    if [ -f "${REPO_NAME}.db.tar.gz" ]; then
        echo "==> 检测到现有数据库，增量添加新包"
        # 删除软链接但保留数据库文件
        rm -f "${REPO_NAME}.db" "${REPO_NAME}.files"
    else
        echo "==> 创建新数据库（完整重建或首次构建）"
        # 删除可能存在的旧文件
        rm -f "${REPO_NAME}.db" "${REPO_NAME}.db.tar.gz" "${REPO_NAME}.files" "${REPO_NAME}.files.tar.gz"
    fi
    
    # 生成/更新仓库数据库
    echo "==> 运行 repo-add"
    repo-add "${REPO_NAME}.db.tar.gz" *.pkg.tar.zst
fi

# 创建软链接（pacman 标准）
ln -sf "${REPO_NAME}.db.tar.gz" "${REPO_NAME}.db"
ln -sf "${REPO_NAME}.files.tar.gz" "${REPO_NAME}.files"

echo "==> 仓库数据库生成完成"
ls -lh "${REPO_NAME}.db"* "${REPO_NAME}.files"*

# 生成包列表 JSON（从数据库中提取完整列表）
echo "==> 生成包列表元数据"

# 从数据库中提取所有包信息
TEMP_DIR=$(mktemp -d)
tar -xzf "${REPO_NAME}.db.tar.gz" -C "$TEMP_DIR"

# 收集所有包的完整文件名
declare -a PACKAGES
for desc_file in "$TEMP_DIR"/*/desc; do
    if [ -f "$desc_file" ]; then
        # 从 desc 文件中提取包的完整名称
        pkg_name=""
        pkg_version=""
        pkg_arch=""
        
        while IFS= read -r line; do
            if [ "$line" = "%NAME%" ]; then
                read -r pkg_name
            elif [ "$line" = "%VERSION%" ]; then
                read -r pkg_version
            elif [ "$line" = "%ARCH%" ]; then
                read -r pkg_arch
            fi
        done < "$desc_file"
        
        if [ -n "$pkg_name" ] && [ -n "$pkg_version" ] && [ -n "$pkg_arch" ]; then
            # 构建完整的包文件名（不含扩展名）
            PACKAGES+=("${pkg_name}-${pkg_version}-${pkg_arch}")
        fi
    fi
done

rm -rf "$TEMP_DIR"

# 生成 JSON
TOTAL_PACKAGES=${#PACKAGES[@]}
echo "==> 数据库中共有 $TOTAL_PACKAGES 个包"

cat > packages.json <<EOF
{
  "build_date": "$(date -Iseconds)",
  "repository": "${REPO_NAME}",
  "total_packages": ${TOTAL_PACKAGES},
  "packages": [
EOF

first=true
for pkg in "${PACKAGES[@]}"; do
    if [ "$first" = true ]; then
        first=false
    else
        echo "," >> packages.json
    fi
    echo -n "    \"$pkg\"" >> packages.json
done

echo "" >> packages.json
echo "  ]" >> packages.json
echo "}" >> packages.json

echo "==> 完成！"
echo "==> 包数量: $TOTAL_PACKAGES"
echo "==> 输出目录: $OUTPUT_DIR"

