# SkorionOS Packages Repository

这是 SkorionOS 的自定义包仓库，包含所有 AUR 和本地包的构建和发布。

## 仓库结构

```
skorion-packages/
├── aur.txt                    # AUR 包列表（跟随最新版本）
├── aur-pinned.txt             # 固定版本的 AUR 包
├── local/                     # 本地包的 PKGBUILD（每个包一个目录）
├── scripts/
│   ├── build-single-package.sh  # 构建单个包（供 CI matrix 使用）
│   └── generate-repo.sh         # 生成仓库数据库
└── .github/workflows/
    └── build.yml              # CI 配置（matrix 并行构建）
```

## 使用方法

### 作为 pacman 仓库

在 `/etc/pacman.conf` 中添加：

```ini
[skorion]
SigLevel = Optional TrustAll
Server = https://github.com/SkorionOS/skorion-packages/releases/download/latest
```

然后：

```bash
sudo pacman -Sy
sudo pacman -S lib32-extest nbfc-linux hhd ...
```

### 锁定特定版本

使用带日期的 Release：

```ini
[skorion]
SigLevel = Optional TrustAll
Server = https://github.com/SkorionOS/skorion-packages/releases/download/2024.11.05
```

## 构建特性

### 版本控制策略

**默认跟随上游**：
- `aur.txt` 中的包默认使用 AUR 最新版本
- 简单、自动，适合大部分包

**选择性固定**：
- `aur-pinned.txt` 中的包固定到特定 git commit
- 适用于需要稳定性的包或有问题的上游版本
- 格式：`包名=commit_hash`（留空表示最新）

**Release 快照**：
- 每次构建生成完整快照
- 带日期的 Release 作为整体版本控制
- 可随时回滚到历史版本

### 增量构建

**智能更新检测**：
- 自动对比 AUR 包版本（通过 AUR RPC API）
- 只构建有更新的包
- 大幅减少构建时间和资源消耗

**完整仓库保证**：
- 从 `latest` Release 下载未更新的包
- 新旧包合并生成完整仓库数据库

### GitHub Actions Matrix 并行构建

**高效构建**：
- 每个包作为独立 job 并行构建
- 最多 20 个 job 同时运行
- 使用 `pikaur` 自动处理 AUR 依赖

**智能依赖处理**：
- 无需手动管理依赖关系
- 失败的包不影响其他包构建

### CI/CD 流程

1. **检测更新**：
   - 下载 `latest` Release 的 `packages.json`
   - 对比每个 AUR 包的版本（通过 AUR RPC）
   - 生成待构建包列表

2. **Matrix 并行构建**：
   - 只构建有更新的包
   - 每个包独立 job，上传 artifact

3. **生成完整仓库**：
   - 下载 `latest` Release 的所有旧包
   - 收集新构建的包（替换同名旧包）
   - 合并成完整的包集合

4. **生成数据库**：
   - 创建 `skorion.db.tar.gz` pacman 数据库
   - 包含所有包（新 + 旧）

5. **发布**：
   - 更新 `latest` Release
   - 每周日创建带日期的归档 Release

## 维护指南

### 添加新包

**AUR 包**：
```bash
# 编辑 aur.txt，添加包名
echo "new-aur-package" >> aur.txt
```

**本地包**：
```bash
# 创建包目录和 PKGBUILD
mkdir -p local/new-package
# 编写 PKGBUILD...
```

### 固定包版本

当某个 AUR 包需要固定版本时：

```bash
# 1. 找到想要的 commit
cd /tmp
git clone https://aur.archlinux.org/package-name.git
cd package-name
git log  # 查看历史，找到合适的 commit

# 2. 添加到 aur-pinned.txt
echo "package-name=abc123def456" >> aur-pinned.txt

# 3. 提交并推送，CI 会使用固定版本构建
```

### 更新包

**AUR 包**：
- `aur.txt` 中的包：CI 自动使用最新版本
- `aur-pinned.txt` 中的包：需要手动更新 commit hash

**本地包**：
- 直接编辑 `local/*/PKGBUILD`
- 推送后 CI 自动重新构建

### 回滚版本

使用带日期的 Release：

```ini
[skorion]
SigLevel = Optional TrustAll
Server = https://github.com/SkorionOS/skorion-packages/releases/download/2024.11.05
```

## License

包遵循各自的许可证。

