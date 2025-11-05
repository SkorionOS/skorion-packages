#!/bin/bash
# Generate pacman repository database from all packages

set -e

OUTPUT_DIR="${OUTPUT_DIR:-./output}"
REPO_NAME="skorion"

echo "==> 生成 pacman 仓库数据库"

cd "$OUTPUT_DIR"

# 统计包数量
PACKAGE_COUNT=$(ls -1 *.pkg.tar.zst 2>/dev/null | wc -l)
echo "==> 找到 $PACKAGE_COUNT 个包"

if [ "$PACKAGE_COUNT" -eq 0 ]; then
    echo "==> 错误: 没有找到任何包"
    exit 1
fi

# 删除旧的数据库文件
rm -f "${REPO_NAME}.db" "${REPO_NAME}.db.tar.gz" "${REPO_NAME}.files" "${REPO_NAME}.files.tar.gz"

# 生成仓库数据库
echo "==> 运行 repo-add"
repo-add "${REPO_NAME}.db.tar.gz" *.pkg.tar.zst

# 创建软链接（pacman 标准）
ln -sf "${REPO_NAME}.db.tar.gz" "${REPO_NAME}.db"
ln -sf "${REPO_NAME}.files.tar.gz" "${REPO_NAME}.files"

echo "==> 仓库数据库生成完成"
ls -lh "${REPO_NAME}.db"* "${REPO_NAME}.files"*

# 生成包列表 JSON
echo "==> 生成包列表元数据"
cat > packages.json <<EOF
{
  "build_date": "$(date -Iseconds)",
  "repository": "${REPO_NAME}",
  "total_packages": ${PACKAGE_COUNT},
  "packages": [
EOF

first=true
for pkg in *.pkg.tar.zst; do
    if [ -f "$pkg" ]; then
        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> packages.json
        fi
        
        # 移除前缀标记和后缀
        pkg_clean=$(echo "$pkg" | sed 's/^\[.*\]-//' | sed 's/\.pkg\.tar\.zst$//')
        echo -n "    \"$pkg_clean\"" >> packages.json
    fi
done

echo "" >> packages.json
echo "  ]" >> packages.json
echo "}" >> packages.json

echo "==> 完成！"
echo "==> 包数量: $PACKAGE_COUNT"
echo "==> 输出目录: $OUTPUT_DIR"

