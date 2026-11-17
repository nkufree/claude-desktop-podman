# GitHub Packages 镜像构建与发布全过程

镜像构建在 **GitHub Actions 的 Linux runner** 中执行。本机只需克隆项目、启动网页和使用 Podman 拉取镜像，无需在本机执行下面的构建过程。

| Package | 用途 | 构建入口 | 发布标签 |
| --- | --- | --- | --- |
| `ghcr.io/nkufree/claude-desktop-podman/claude-desktop` | 带 Xfce、Chromium、主题、Claude Desktop、noVNC 的网页桌面 | `desktop/Containerfile` | `latest`、`sha-提交完整SHA`，支持 AMD64 / ARM64 |

本项目仅构建和发布 `claude-desktop` 桌面镜像。

## 触发构建

1. 打开仓库的 [Build and Push Images](https://github.com/nkufree/claude-desktop-podman/actions/workflows/build-and-push.yml)。
2. 点击 **Run workflow**，选择 `main`。桌面 `release` 填 `latest` 或上游社区发布的完整标签。
3. 等待两个 `Desktop` 架构任务完成，再等待桌面多架构发布任务完成。
4. 在仓库 [Packages](https://github.com/nkufree?tab=packages) 查看结果。新 package 若为 Private，免登录使用前需由所有者在设置中改为 Public。

推送 `main` 和每日定时任务也会触发构建。Pull request 仅验证构建，不登录 GHCR、不推送镜像。各步骤的完整命令和输出可在 Actions 展开查看。

## 网页桌面：全部构建步骤

桌面使用两个原生 runner：AMD64 为 `ubuntu-latest`，ARM64 为 `ubuntu-24.04-arm`。流程定义见 [.github/workflows/build-and-push.yml](../.github/workflows/build-and-push.yml)。

1. 检出项目源码，初始化 Docker Buildx。
2. 在 runner 上使用 GitHub API 查询 `aaddrick/claude-desktop-debian` 的指定 release，按架构选择真实的 `claude-desktop-unofficial` Debian 包，并取得公开下载地址和 SHA256。API 使用 Actions 令牌；传给镜像构建的只有公开安装包信息，令牌不会进入镜像。
3. 非 PR 构建登录 `ghcr.io`，使用仓库自动提供的 `GITHUB_TOKEN`。
4. 以 `desktop/` 为构建上下文，按 [desktop/Containerfile](../desktop/Containerfile) 完成以下全部层：
   - 从 `debian:bookworm-slim` 开始，设置 Debian 软件源并执行 `apt-get update`。
   - 安装 Xfce、终端、D-Bus、TigerVNC、noVNC、websockify、Chromium、密钥环、字体、语言包、时区、主题依赖和 tini。
   - 生成英文 `en_US.UTF-8` locale，清理 APT 缓存。
   - 执行 `install-theme.py`，下载并校验固定提交的 Fluent 主题，安装窗口主题与图标。
   - 执行 `install-claude.py`，下载对应架构的 `.deb` 并校验 SHA256，再用 APT 安装 Claude Desktop 及其依赖。
   - 创建 UID 1000 的 `claude` 用户，复制桌面启动、浏览器、Claude、主题配置脚本及快捷方式，设置可执行权限和 URL 处理器。
   - 设置英文环境、美国纽约时区、1920×1080 默认分辨率，切换到非 root 用户。
   - 设置 `6080` 端口、桌面健康检查和 tini / `start-desktop` 启动入口。
5. 发布各架构临时标签 `sha-提交SHA-amd64`、`sha-提交SHA-arm64`，通过 GitHub Actions cache 缓存依赖层。
6. 两个架构都构建成功后，运行以下命令发布多架构索引：

```sh
IMAGE="ghcr.io/${GITHUB_REPOSITORY,,}/claude-desktop"
docker buildx imagetools create \
  --tag "$IMAGE:latest" \
  --tag "$IMAGE:sha-$GITHUB_SHA" \
  "$IMAGE:sha-$GITHUB_SHA-amd64" \
  "$IMAGE:sha-$GITHUB_SHA-arm64"
```

`CLAUDE_INSTALL_REV` 每次 workflow 更新，使应用安装步骤重新执行；其余未变层使用缓存。镜像构建不会读取或打包宿主机登录资料，登录数据在运行时挂载到 `/home/claude`。

## 拉取并使用构建结果

本机只拉取构建结果：

```sh
# 完整桌面镜像，自动选择 AMD64 / ARM64
podman pull ghcr.io/nkufree/claude-desktop-podman/claude-desktop:latest

# 检查版本、入口、架构和文档链接
podman image inspect ghcr.io/nkufree/claude-desktop-podman/claude-desktop:latest
```

网页启动 `./bin/claude-desktop` 后，镜像来源选择「使用预构建镜像」，保留默认桌面镜像地址并启动即可。私有 package 需先执行 `podman login ghcr.io`。
