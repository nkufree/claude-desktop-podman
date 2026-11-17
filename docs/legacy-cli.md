# Claude Code 命令行说明

本文介绍 `bin/claude` 提供的旧版 Claude Code 命令行环境。需要网页配置、Linux 图形桌面或在容器浏览器中登录时，请使用主 [README](../README.md) 中的 `./bin/claude-desktop`。

命令行启动器在无根 Podman 容器内运行 Claude Code，默认镜像为本仓库发布的 `ghcr.io/nkufree/claude-desktop-podman/claude-code:latest`。每次启动检查更新；离线时使用已缓存的镜像。它与网页桌面的镜像、登录存储和配置相互独立。

## 安装与启动

先安装 [Podman](https://podman.io/docs/installation)，然后在项目目录执行：

```sh
make install
# 或仅安装 Claude Code 启动器
make install-claude

# 安装到用户目录，无需 sudo
make PREFIX="$HOME/.local" SUDO=
```

默认安装路径为 `/usr/local/bin/claude-podman`，可通过 `PREFIX` 或 `BINDIR` 修改。`make uninstall` 移除该启动器。

进入要处理的项目目录后启动：

```sh
claude-podman
# 也可直接使用项目中的脚本
./bin/claude
```

`claude-podman --self-update` 从本仓库更新启动器，也可通过重新执行 `make install` 安装。

## 文件挂载与权限

| 内容 | 宿主机路径 / 行为 |
| --- | --- |
| 工作目录 | 当前目录 `$PWD`，可读写 |
| 登录资料和运行数据 | `$HOME/.claude`、`$HOME/.claude.json`，可读写 |
| Claude 配置 | `~/.claude` 下的 `settings.json`、`CLAUDE.md`、`plugins/`、`agents/`、`commands/`、`skills/`，只读挂载 |
| Git 配置 | `~/.gitconfig`、`~/.config/git/config`，存在时只读挂载 |

容器以非 root 用户运行，通过 `--userns=keep-id` 保持工作目录中文件的宿主机所有权。未挂载的宿主机目录、SSH 密钥和浏览器资料不会自动出现在容器中。Git 配置用于保留提交署名和别名，不会自动提供宿主机的 Git 登录凭据。

只读配置应在宿主机修改。工作目录、`~/.claude.json` 和其他可写挂载仍会影响宿主机文件；在宿主机执行容器修改过的项目脚本前，应检查改动。

## 登录

这个命令行容器没有浏览器。可使用以下任一方式登录：

```sh
# 在宿主机启动 Claude Code 后执行 /login，复用挂载的登录资料
claude

# 在容器中使用 /login，打开输出的网址并粘贴认证码
claude-podman

# 使用宿主机浏览器，并转发 OAuth 回调到容器
claude-podman --login
```

`--login` 需要支持相应参数的 pasta 网络后端。Claude 的回调使用随机端口，启动器通过 `pasta -t auto` 转发容器监听端口，并关闭该会话的 IPv6。该模式会在宿主机 IPv4 地址上转发容器监听的所有端口，登录后应退出并以普通模式重新启动。

网页桌面具有自己的容器浏览器，登录方式见主 README，无需先在宿主机登录。

## 运行选项

| 选项 | 作用 |
| --- | --- |
| `--local` | 使用本地构建的 `claude-code:latest` 镜像 |
| `--apk-packages LIST` | 启动时安装额外 Alpine 软件包，以逗号或空格分隔 |
| `--init-script FILE` | 将初始化脚本复制到容器并以 root 身份执行 |
| `--podman-arg ARG` | 向 `podman run` 添加一个参数，可重复使用 |
| `--self-update` | 从原上游 GitHub 仓库更新启动器 |
| `--help` | 显示用法 |
| `--yolo` | 传入 `--dangerously-skip-permissions` 并跳过一次性提示，容器成为主要隔离边界 |
| `--login` | 打开宿主机浏览器并转发随机 OAuth 回调端口 |

启动器选项必须放在 Claude 参数前面。第一个无法识别的参数及后续参数会原样传给 Claude。

默认保留 Claude 的权限确认行为，用户自身的 `permissions` 或 `defaultMode` 配置仍会生效。`--yolo` 会取消权限提示，可写挂载内容也会随之允许修改。

额外软件包和初始化脚本会在每次启动时执行；失败时停止启动。参考 [examples/init.sh](../examples/init.sh) 编写初始化脚本。

```sh
# 镜像默认未安装 git
claude-podman --apk-packages git

# 安装 kubectl，并只读挂载 Kubernetes 配置
claude-podman \
  --apk-packages kubectl \
  --podman-arg "--volume=$HOME/.kube/config:/home/claude/.kube/config:ro"

# 从管道传入内容
git diff | claude-podman -p "审查这些改动"
```

每次 `--podman-arg` 只传递一个参数，例如 `--podman-arg "--volume=/path:/workspace"`；也可用 `--podman-arg -v --podman-arg /path:/workspace`。不要将 `-v /path:/workspace` 拼成一个参数。

Claude 退出、终端关闭或初始化时按 Ctrl+C 后，临时容器会被删除；挂载目录中的资料会保留。

## 本地构建与自定义镜像

预构建 package 的发布由 GitHub Actions 完成，无需在本地构建。完整步骤（基础镜像、安装 Claude Code、创建用户、设置入口、提交镜像、版本标签和 GHCR 推送命令）见 [镜像构建与发布全过程](build-images.md)。下方仅保留用户需要自定义本地镜像时的用法。

命令行镜像使用 Buildah 构建，宿主机还需要 npm 和 jq：

```sh
make legacy-images
# 等同于
make image-claude
# 也可直接执行
./devops/build-claude.sh

claude-podman --local
```

构建脚本仅向标准输出打印镜像引用（例如 `claude-code:<版本>`），其他日志写入标准错误，供自动构建流程读取。

安装编译工具链等较大的依赖时，建议写入自定义基础镜像：

```sh
make legacy-images CONTAINERFILE=examples/Containerfile.rust-nightly
claude-podman --local
```

也可将自己的文件放在项目根目录 `Containerfile`（已忽略，不纳入 Git），`make legacy-images` 会自动使用它。指定 `CONTAINERFILE=` 可恢复原始基础镜像。自定义文件可从以下内容开始：

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
```

默认基础镜像为 `node:current-alpine`，Claude Code 安装在自定义层之上。保留 Alpine 和 npm 环境，避免占用基础镜像的 UID 1000 和 Claude 用户的 UID 1001。

| 环境变量 | 作用 |
| --- | --- |
| `BASE_IMAGE` | 替换默认基础镜像 |
| `CONTAINERFILE` | 先在基础镜像上构建自定义层，再安装 Claude Code |
| `NO_CACHE=1` | 忽略自定义层缓存重新构建；默认缓存有效期为一周 |

自定义构建会替换本地 `claude-code:latest`，之后 `--local` 使用最后构建的版本。保留 Cargo 下载缓存时，可使用独立具名卷：

```sh
claude-podman --local --podman-arg "--volume=cargo-home:/home/claude/.cargo:U"
```

## 许可证

本项目采用 [AGPL-3.0-or-later 许可证](https://www.gnu.org/licenses/agpl-3.0.html)。
