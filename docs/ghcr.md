# 发布和使用 GitHub Container Registry 镜像

仓库在推送 `v*` 格式的 Git tag 时，会自动构建并推送多架构镜像到 GitHub Container
Registry（GHCR）。镜像名称为：

```text
ghcr.io/tingfeng347/windcode
```

工作流使用 GitHub Actions 自动提供的 `GITHUB_TOKEN`，并只授予 `contents: read` 和
`packages: write` 权限；请不要把个人访问令牌（PAT）写入仓库或 Actions secret。

## 国内镜像源

Dockerfile 默认通过 DaoCloud 国内镜像站拉取 Docker Hub 的官方 Python 3.11 slim 基础镜像：

```text
m.daocloud.io/docker.io/library/python:3.11-slim
```

需要在无法访问该镜像站的海外环境构建时，可以显式回退到 Docker Hub：

```bash
docker build --build-arg PYTHON_IMAGE=python:3.11-slim -t windcode:local .
```

## 发布版本

先确保待发布提交已经推送到 GitHub，再创建并推送一个与项目版本对应的 tag：

```bash
git tag v0.2.2
git push origin v0.2.2
```

随后在仓库的 **Actions** 页面查看 `Publish container image` 工作流。首次发布的 GHCR
包默认为私有；如需允许匿名拉取，请在 GitHub 的包设置中将其改为 Public。

镜像会包含以下 tag：

- `0.2.2`、`0.2`、`0`：由语义化版本 tag 生成。
- `sha-...`：对应提交的短 SHA。
- `latest`：仅在公开仓库的版本 tag 发布时生成。

## 运行

Windcode 是交互式终端程序，运行时请保留 TTY，并挂载需要处理的代码目录：

```bash
docker run --rm -it \
  -v "$PWD:/workspace" \
  ghcr.io/tingfeng347/windcode:0.2.2
```

默认会打开挂载的 `/workspace`。也可以明确指定容器内的项目路径：

```bash
docker run --rm -it \
  -v /absolute/path/to/project:/workspace \
  ghcr.io/tingfeng347/windcode:0.2.2 \
  /workspace
```

镜像会在启动时读取 `/workspace` 的 UID/GID，并使用同一身份运行 Windcode。这样项目内已存在的
`.windcode/` 状态目录（包括记忆索引）可以正常读写，也不会在宿主机上产生 root 属主的文件。
不要使用 `--user root` 覆盖该行为；如需排查容器环境，可运行：

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/tingfeng347/windcode:0.2.2 sh -c 'id'
```

交互启动时，镜像会将 Docker 默认的 `TERM=xterm` 升级为 `xterm-256color`，并在未提供
`COLORTERM` 时启用 `truecolor`，以保留 Windcode 欢迎页的彩色样式。若终端本身不支持真彩色，
可在运行时覆盖，例如 `-e COLORTERM=`。

容器使用非 root 用户运行。若 Windcode 需要持久化会话、记忆和扩展状态，可额外挂载其状态目录：

```bash
docker run --rm -it \
  -v "$PWD:/workspace" \
  -v windcode-state:/home/windcode/.windcode \
  ghcr.io/tingfeng347/windcode:0.2.2
```

## 私有镜像拉取

私有镜像需要使用具有 `read:packages` 权限的 classic PAT 登录。将 PAT 保存在本地环境变量中，
不要把它传入 Dockerfile、镜像层或 Git 仓库：

```bash
printf '%s' "$CR_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
docker pull ghcr.io/tingfeng347/windcode:0.2.2
```

发布应优先由本仓库工作流完成。GitHub 会把通过 `GITHUB_TOKEN` 发布的包自动关联到该仓库；
镜像也包含 `org.opencontainers.image.source` 标签，以保留源码关联信息。
