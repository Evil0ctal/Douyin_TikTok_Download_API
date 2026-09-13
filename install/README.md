# One-command install / 一键部署

**English below · 中文见下半部分**

Two scripts, same behaviour, different language. Both install with **Docker
only** — if you want to run without Docker, that is a document rather than a
script: [documents/en/02-installation.md](../documents/en/02-installation.md).

| | Script | Docs |
|---|---|---|
| English | [`install.sh`](./install.sh) | [Installation](../documents/en/02-installation.md) |
| 中文 | [`install.zh.sh`](./install.zh.sh) | [安装与部署](../documents/zh/02-installation.md) |

---

## Read it before you run it

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh -o install.sh
less install.sh          # 1500 lines, every one of them commented
bash install.sh
```

That is the recommended order, and it is not ceremony. Anything you pipe into a
shell runs as you, and on the Docker step it offers to run something as root.
The script is written to be read: it says what it is about to do before each
step and asks before every change.

The single line, if you would rather:

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh | bash
```

Piping still works, including the questions — the script reads answers from your
terminal rather than from stdin, because with `curl | bash` stdin is the script
itself. If there is no terminal at all it says so instead of quietly taking
defaults.

## Run it again to manage the instance

The script works out which job it is doing. If there is already an install —
found by asking Docker, so it does not matter where you put it — it opens a
menu instead of installing:

```
==> An install is already here
    ✓ /opt/dtk
    ✓ Running 5.0.0
    ! v5.0.1 is available.

      1  Status - versions, containers, disk
      2  Upgrade to v5.0.1
      3  Manage - passwords, backups, settings, disk
      4  Stop or remove this install
      q  Quit
```

**Upgrading** compares the running version against the latest GitHub release —
a pre-release suffix counts as older, so `5.1.0.dev0` is behind `5.1.0`. It
pins `DTK_IMAGE_TAG` to the version rather than following `latest`, so you can
say what is running and put it back. The data is untouched: the named volumes
survive, and if migrations fail the old containers are still up and nothing was
swapped.

**Manage** covers the things people otherwise ask how to do:

| | |
|---|---|
| Change a password | `dtk user passwd`, read without echo and passed on stdin |
| Add an administrator | There is no rename — accounts are created and reset, not renamed |
| List accounts | |
| Back up / restore | Into the backup volume, which survives a rebuild |
| Self check | `dtk diagnose`, the six-step one |
| Logs, restart | |
| Settings | Any runtime setting, including `retention.*` — no restart needed |
| Free disk space | Stale images from **this project only**, and optionally build cache |

That last one is worth a note. Container logs are already capped by the compose
file at 10 MB × 3 per service, so the whole stack cannot pass about 180 MB of
logs. What grows without a ceiling is old image tags and buildx cache, and the
cleanup is scoped to this project's images — a plain `docker image prune -a`
would take unrelated images off a host running other stacks.

**Stop or remove** has three levels: stop and keep everything, stop and delete
the volumes, or that plus the install directory. The last two require typing
`delete`, not a y/n, and the directory removal refuses anything that does not
look like an install.

`--manage` goes straight there.

## What it asks

Six questions, all with a default you can accept by pressing enter:

1. **Where to install.** `/opt/dtk` as root, `~/dtk` otherwise.
2. **Which address and port.** `127.0.0.1:8000`. Choosing `0.0.0.0` warns you,
   because that puts an unencrypted admin console on the network.
3. **The browser container.** It mints guest identities by itself so you never
   paste cookies, and it costs about 4 GiB of RAM and a 6 GB image built on this
   machine. Suggested off under 6 GiB of RAM, and always off with `--yes`.
4. **Media downloads.** Small and cheap. On by default.
5. **Published images or build from source.** Published, unless you say
   otherwise.
6. **Update the checkout**, if there is already an install in that directory.

## What it does

- Detects the distribution and offers the right way to install Docker. Debian,
  Ubuntu and derivatives; RHEL, CentOS, Rocky, AlmaLinux, Fedora, Oracle,
  Amazon Linux; openSUSE and SLES; Arch and Manjaro; Alpine; NixOS, Void,
  Gentoo. macOS points at Docker Desktop, WSL points at the Windows side.
- Refuses to guess on 32-bit or anything that is not x86_64 or arm64.
- **Scales the resource limits to this machine.** `docker/compose.yml` is
  written for 4 vCPU and 8 GiB. Docker refuses outright to start a container
  asking for more CPUs than exist, so on a smaller host the script writes a
  `compose.host.yml` with ceilings that fit.
- Generates every secret with `openssl rand`, writes `.env` under `umask 077`,
  and never prints one.
- Writes a `dtkctl` wrapper that carries the three arguments which have to be
  right every time — the project name, both compose files, and
  `COMPOSE_ENV_FILES`, without which Compose reads `docker/.env` and silently
  interpolates defaults.
- Pulls, migrates, starts, waits for every service to report healthy, and prints
  the one-time link that creates the first administrator.

## What it will not do

It writes only inside the directory you choose, plus Docker's own volumes. It
never edits a file outside that directory, never adds a cron job, never opens a
firewall port, never installs anything without asking.

It uses `sudo` for exactly three things, each printed before it runs: installing
Docker if you say yes, starting the Docker service, and creating the install
directory when that needs root. It will not add you to the `docker` group — on
most machines that is the same as handing out root, so it tells you the command
and lets you decide.

It refuses to install into `/`, `/etc`, `/usr`, `/var`, `/home` and friends, into
a directory that is a checkout of some other project, or into a directory that
already has something in it.

## Options

```
--yes, -y   Take every default. Publishes on 127.0.0.1:8000, downloader on,
            browser container off.
--check     Detect the system and print what would happen, then stop. Changes
            nothing — good for seeing what it thinks of your machine first.
--manage    Go straight to the menu for an install that already exists.
--help, -h  Everything above, shorter.
```

Every answer can be preset from the environment, which is what makes `--yes` a
real unattended install:

```bash
DTK_INSTALL_DIR=/srv/dtk DTK_BIND_PORT=9000 DTK_ENABLE_BROWSER=1 \
  bash install.sh --yes
```

`DTK_PROJECT` names the Compose project (default `dtk`). Set it to run a second,
completely separate stack on the same host.

## Requirements

`bash`, `git`, `curl`, and either `openssl` or `/dev/urandom`. Alpine ships none
of bash or git by default: `apk add bash git curl`.

Docker Compose **v2.24 or newer** — the compose file uses the long `env_file:`
form and `COMPOSE_ENV_FILES`, and older versions fail in ways that look like a
problem with this project.

## Installing from mainland China

Switch your mirrors first. The install pulls from Docker Hub, and building the
browser image also reaches GitHub, PyPI and a browser CDN. Without a registry
mirror the image pull usually times out and it looks like the project is broken.
[中国大陆的网络准备](../documents/zh/02-installation.md#中国大陆的网络准备) has
the details.

---

# 一键部署

两个脚本，行为相同，语言不同。两者都**只支持 Docker** —— 不用 Docker 的部署方式是
一篇文档能讲清的事，不是一个脚本：[安装与部署](../documents/zh/02-installation.md)。

## 先读，再运行

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh -o install.zh.sh
less install.zh.sh       # 一千五百行，每一行都有注释
bash install.zh.sh
```

推荐这个顺序，而且这不是走过场。任何你管道进 shell 的东西都以你的身份运行，而在
装 Docker 那一步，它还会问你要不要以 root 跑一个脚本。这个脚本是写来给人读的：
每一步之前都会说清自己要做什么，每一次改动之前都会先问。

想要真正的一行命令也可以：

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh | bash
```

管道运行时提问依然正常 —— 脚本是从你的终端读答案的，不是从标准输入读，因为
`curl | bash` 的时候标准输入就是脚本本身。如果压根没有终端，它会明说，而不是
默默取默认值。

## 再跑一次就是管理工具

脚本自己判断该做哪件事。如果已经有一份安装 —— 位置是问 Docker 要的，所以装在哪
都能找到 —— 它打开菜单而不是重新安装：

```
==> 这里已经有一份安装了
    ✓ /opt/dtk
    ✓ 运行中：5.0.0
    ! 有新版本 v5.0.1。

      1  状态 —— 版本、容器、磁盘
      2  升级到 v5.0.1
      3  管理 —— 口令、备份、设置、磁盘
      4  停止或移除这份安装
      q  退出
```

**升级**会把运行中的版本和 GitHub 最新 Release 比一下 —— 带预发布后缀的算旧，
所以 `5.1.0.dev0` 排在 `5.1.0` 前面。它把 `DTK_IMAGE_TAG` 钉在具体版本上而不是
跟着 `latest` 跑，这样你说得出现在跑的是哪个，也换得回去。数据不受影响：命名卷
不会消失；万一迁移失败，旧容器还在跑，什么都没被替换。

**管理**覆盖的就是大家平时会来问怎么做的那些事：

| | |
|---|---|
| 修改口令 | 走 `dtk user passwd`，不回显读入，从标准输入喂进去 |
| 添加管理员 | 没有改名功能 —— 账号只能新建和重置，不能改名 |
| 账号列表 | |
| 备份 / 恢复 | 写进备份卷，重建容器不会丢 |
| 自检 | `dtk diagnose`，六步那个 |
| 日志、重启 | |
| 设置 | 任何运行时设置，包括 `retention.*`，改完不用重启 |
| 释放磁盘空间 | **只针对本项目**的陈旧镜像，构建缓存可选 |

最后一项值得说一句。容器日志已经被 compose 文件按每服务 10 MB × 3 封顶了，整套栈
的日志过不了 180 MB 左右。真正没有上限、会一直涨的是旧的镜像 tag 和 buildx 缓存；
而这里的清理只针对本项目的镜像 —— 直接 `docker image prune -a` 会把同一台机器上
别的栈的镜像一起端掉。

**停止或移除**分三档：停掉但什么都不删、停掉并删数据卷、以及在此基础上连安装目录
一起删。后两档要求打出 `delete` 而不是 y/n，删目录那一步还会拒绝任何看着不像安装
的路径。

加 `--manage` 可以直接进这个菜单。

## 它会问什么

六个问题，每个都有默认值，回车即可：

1. **装到哪里。** root 是 `/opt/dtk`，否则 `~/dtk`。
2. **发布在哪个地址和端口。** 默认 `127.0.0.1:8000`。选 `0.0.0.0` 会给你警告，
   因为那等于把一个不加密的管理后台放到网络上。
3. **要不要浏览器容器。** 它自己铸造游客身份，你就不用手动粘 Cookie；代价是大约
   4 GiB 内存和一个要在本机构建的 6 GB 镜像。内存不到 6 GiB 时建议关，
   加了 `--yes` 时一律不建。
4. **要不要媒体下载。** 很小很省，默认开。
5. **用已发布镜像还是从源码构建。** 默认用已发布的。
6. **要不要更新代码**，前提是那个目录里已经有一份安装。

## 它做了什么

- 识别发行版，并给出对应的 Docker 安装方式。覆盖 Debian、Ubuntu 及其衍生版；
  RHEL、CentOS、Rocky、AlmaLinux、Fedora、Oracle、Amazon Linux；openSUSE 和
  SLES；Arch 和 Manjaro；Alpine；NixOS、Void、Gentoo。macOS 指向 Docker
  Desktop，WSL 指向 Windows 那一侧。
- 遇到 32 位或者非 x86_64 / arm64 的架构就直说，不瞎猜。
- **按这台机器缩放资源上限。** `docker/compose.yml` 是按 4 vCPU / 8 GiB 写的。
  容器要的核数超过实际核数时 Docker 会**直接拒绝启动**，所以在更小的机器上，
  脚本会生成一份合身的 `compose.host.yml`。
- 所有密钥用 `openssl rand` 生成，`.env` 在 `umask 077` 下写出，从不打印任何一个。
- 生成一个 `dtkctl` 包装脚本，替你带上那三个每次都得对的参数 —— 项目名、两个
  compose 文件，以及 `COMPOSE_ENV_FILES`；没有最后这个，Compose 会去读
  `docker/.env` 然后悄悄用默认值做插值。
- 拉镜像、跑迁移、启动、等所有服务报告健康，最后打印出创建第一个管理员的
  一次性链接。

## 它不会做什么

它只往你指定的那个目录里写东西，外加 Docker 自己的数据卷。不改那个目录之外的
任何文件，不加定时任务，不开防火墙端口，不问过你就不装任何东西。

它只在三件事上用 `sudo`，每一次都会先把命令原样打出来：你答应之后安装 Docker、
启动 Docker 服务、以及创建那个需要 root 才能建的安装目录。它**不会**把你加进
`docker` 组 —— 在大多数机器上那基本等同于给出 root 权限，所以它把命令告诉你，
由你自己决定。

它拒绝装到 `/`、`/etc`、`/usr`、`/var`、`/home` 这类目录，拒绝装进别的项目的
git 仓库，也拒绝装进一个已经有东西的目录。

## 选项

```
--yes, -y   全部取默认值。发布在 127.0.0.1:8000，开下载器，不建浏览器容器。
--check     只探测系统并打印将会发生什么，然后停下。不改任何东西 ——
            适合先看看它对你这台机器的判断。
--manage    直接进入已有安装的管理菜单。
--help, -h  上面这些，更短的版本。
```

每个答案都能用环境变量预设，这才让 `--yes` 成为真正的无人值守安装：

```bash
DTK_INSTALL_DIR=/srv/dtk DTK_BIND_PORT=9000 DTK_ENABLE_BROWSER=1 \
  bash install.zh.sh --yes
```

`DTK_PROJECT` 指定 Compose 项目名（默认 `dtk`）。设成别的值，就能在同一台机器上
跑第二套完全独立的栈。

## 前置要求

`bash`、`git`、`curl`，以及 `openssl` 或 `/dev/urandom` 二者之一。Alpine 默认
两样都没有：`apk add bash git curl`。

Docker Compose **2.24 或更新** —— compose 文件用到了长格式的 `env_file:` 和
`COMPOSE_ENV_FILES`，更老的版本失败的样子会让你以为是这个项目有问题。

## 在中国大陆安装

先换源。安装过程要从 Docker Hub 拉镜像，构建浏览器镜像还要访问 GitHub、PyPI 和
浏览器 CDN。没有镜像加速器的话，拉镜像那步通常会超时，看起来就像项目坏了。
详见 [中国大陆的网络准备](../documents/zh/02-installation.md#中国大陆的网络准备)。
