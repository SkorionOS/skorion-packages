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

echo "==> 完成！"
echo "==> 包数量: $PACKAGE_COUNT"
echo "==> 输出目录: $OUTPUT_DIR"

