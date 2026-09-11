# 安装与部署

这篇文档带你从一台空机器走到一个可以放着不管的实例，并把 compose 文件讲清楚到你敢改它的程度。读完之后，
你应该能够：估算机器规格、决定那两个可选容器到底要不要、在 API 前面挂上 TLS、在不丢数据的前提下升级，
以及在你更喜欢的情况下完全不用 Docker 跑起来。

如果你只想用最短的路径先跑起来，先看 [快速开始](./01-quickstart.md)，等到需要改点什么时再回到这里。

## 走哪条路

安装有两条路，先选一条再往下读。

| | Docker Compose | 手动部署 |
|---|---|---|
| 适合谁 | 绝大多数人，生产也包括在内 | 不能用 Docker 的环境，或者你要改代码 |
| 宿主机上要自己装 | 只有 Docker | PostgreSQL 17 + TimescaleDB、Redis 8、Python 3.12、uv；要构建控制台还得有 Node 22 |
| 起一套要多久 | 拉镜像的时间 | 半小时起步，其中一半花在 TimescaleDB 上 |
| 升级 | 改 tag、`pull`、跑迁移、`up -d` | `git pull`、`uv sync`、跑迁移、重启 systemd |
| 资源上限、只读根文件系统、能力裁剪、非 root 用户 | compose 文件里已经写好 | 你自己用 systemd 补回来 |
| 出问题来问我 | 我这边能一比一复现 | 你那台机器是独一份，我只能猜 |
| 从这里开始读 | [首次安装](#首次安装) | [不用 Docker 运行](#不用-docker-运行)（开发）、[在裸机上做生产部署](#在裸机上做生产部署)（生产） |

**推荐 Docker**，理由不是容器时髦，而是这套东西对环境挑剔的地方，compose 文件已经替你固定住了：数据库必须是带
TimescaleDB 扩展的 Postgres 17（普通 `postgres:17` 镜像不行），浏览器镜像里的 CloakBrowser 钉在具体某个 commit 上，
四个服务的启动顺序靠健康检查串起来，每个容器的内存、CPU、PID 上限和只读根文件系统都是写死的。手动部署这些都得你自己
重建一遍——[在裸机上做生产部署](#在裸机上做生产部署) 那一节把要补的东西逐条列了出来，翻过去看一眼要补多少，
再决定走哪条路也不迟。

### 全文目录

**先做准备**

- [用脚本装和管](#用脚本装和管) —— 引导式安装，之后也是这套实例的运维菜单
- [你需要准备什么](#你需要准备什么) —— 机器规格、磁盘、以及换自己的数据库前必须知道的两件事
- [这套默认值是按什么机器写的](#这套默认值是按什么机器写的) · [三档配置](#三档配置) · [怎么改这些上限](#怎么改这些上限)
- [中国大陆的网络准备](#中国大陆的网络准备) —— 从大陆机器上装，先换源，不换大概率卡在拉镜像那步
- [整体结构一览](#整体结构一览) —— 一共几个容器、谁跟谁说话

**Docker 部署**

- [首次安装](#首次安装) —— 从空目录到能访问控制台
- [compose 文件逐个服务讲解](#compose-文件逐个服务讲解) —— 改它之前先读这节
- [两个可选 profile](#两个可选-profile) —— `browser` 和 `downloader` 到底要不要开
- [构建浏览器镜像：CloakBrowser 版本固定](#构建浏览器镜像cloakbrowser-版本固定) —— 这个镜像不发布，只能本地构建
- [环境变量](#环境变量) —— 包括 compose 读 `.env` 的两种方式，踩过的人都记得
- [数据卷](#数据卷) · [端口与监听地址](#端口与监听地址) · [放在反向代理后面](#放在反向代理后面)
- [验证安装](#验证安装) —— 装完跑这几条，别靠"页面打得开"判断
- [升级](#升级) · [扩容：增加 worker](#扩容增加-worker)

**手动部署**

- [不用 Docker 运行](#不用-docker-运行) —— 开发方式：依赖用 uv 装，数据库和 Redis 仍然用容器
- [在裸机上做生产部署](#在裸机上做生产部署) —— 全手工，含 systemd unit，在干净的 Ubuntu 24.04 上实跑验证过
- [单独运行 worker](#单独运行-worker)

**出问题的时候**

- [推倒重来](#推倒重来) —— 保留数据地停，和连数据一起删
- [接下来看什么](#接下来看什么)

## 你需要准备什么

| 要求 | 原因 |
|---|---|
| Docker Engine 带 Compose v2（v2.24 或更新） | Compose 是在 v2.24 加入长格式 `env_file: - path: … required: false`（`docker/compose.yml` 用到了它）和 `COMPOSE_ENV_FILES`（本文会建议用它）的。仓库里并没有任何地方强制这个下限，它来自所用到的特性，而不是一条声明出来的最低版本 |
| Linux、macOS 或 WSL2 宿主机 | 整套东西没有平台相关的部分，但浏览器容器很吃内存，在 Linux 上最省心 |
| 核心栈 2 GiB 内存，稳妥起见 4 GiB | 实际用 2 GiB 就够——四个核心容器静息实测合计约 295 MiB（见下表）——但各容器上限加起来是 3.5 GiB，所以 4 GiB 是稳妥的数字 |
| 启用 `browser` profile 则约 8 GiB 内存 | 浏览器容器上限 4 GiB，而且它是真的会用满 |
| 约 15 GB 空闲磁盘 | 核心栈的镜像约 4.5 GB，加上浏览器容器约 6.3 GB，这还不含数据卷和构建缓存；再加上 Postgres，以及启用媒体下载后的媒体卷（默认上限 2 GiB）。留 15 GB 空闲比较从容 |
| 出站 HTTPS | worker 和浏览器容器要访问平台，通常还要走你自己提供的代理 |

宿主机上**不需要**安装 Postgres、Redis、Python、Node 或浏览器。compose 文件把它们全带上了，而且都固定了版本。

在你打算换成自己已有的数据库之前，有两件事值得先知道：

- 表结构需要 **TimescaleDB** 扩展。请求日志、身份事件和内容快照都是超表（hypertable），第一个迁移脚本会检查
  `pg_available_extensions`，缺了就带着解释拒绝执行，而不是建出半套表。compose 文件用的是
  `timescale/timescaledb-ha:pg17`，普通的 `postgres:17` 镜像不行。
- 仓库里不带任何默认密码或密钥。`DTK_SECRET_KEY` 缺失或短于 32 个字符时，entrypoint 会以 `78`
  （`EX_CONFIG`）退出；`REDIS_PASSWORD` 为空时 redis 容器也会退出。这是刻意的：在每一份安装里都相同的
  密钥，等于没有加密。

资源上限和实测占用，都来自 `docker/compose.yml`（其注释里的实测数据取自 2026-09-08）：

| 服务 | `mem_limit` | `cpus` | `pids_limit` | 静息实测 |
|---|---|---|---|---|
| `postgres` | 2g | — | 256 | 112 MiB |
| `redis` | 512m | — | 128 | 8 MiB |
| `api` | 512m | 2.0 | 256 | 104 MiB |
| `worker` | 512m | 2.0 | 256 | 71 MiB |
| `browser-rpc` | 4g | 4.0 | 1024 | 热态 2.57 GiB、629% CPU |
| `downloader` | 512m | 2.0 | 128 | 只有流式传输的缓冲区 |

这些上限的存在，是为了让一个过载的组件没法把其余部分一起拖垮。`postgres` 是例外：它没有 CPU 上限、内存上限
也给得宽，因为对 Postgres 收紧上限损害的是正确性而不是保护了谁——那 2g 是为了不让一条失控的查询拖垮宿主机，
不是让它在这个预算里过日子。

### 这套默认值是按什么机器写的

把上表的上限加起来：**约 8 GiB 内存**，而 `browser-rpc` 要 4 个核。也就是说
`docker/compose.yml` 描述的是一台 **4 vCPU / 8 GiB** 的机器——在那种机器上直接
`up -d`，什么都不用改。

上限是天花板不是预留，所以日常占用远低于它。但**它在小机器上不是"慢一点"，是起不来**：

```
Error response from daemon: range of CPUs is from 0.01 to 2.00,
as there are only 2 CPUs available
```

Docker 拒绝启动一个要求的核数多于宿主机的容器。这是硬报错，不会自动降级。

### 三档配置

| 档位 | 规格 | 能做什么 | 要不要改 compose |
|---|---|---|---|
| 不跑浏览器 | 1 vCPU / 2 GiB | 手动导入 cookie，无自动铸造。四个核心容器静息合计约 295 MiB | 不用，别加 `--profile browser` 就行 |
| 完整（最低） | 2 vCPU / 4 GiB **+ swap** | 全部功能 | **要**，见下面的覆盖文件 |
| 完整（推荐） | 4 vCPU / 8 GiB | 全部功能 | 不用 |

最低那一档是实测过的，不是估的。一台 2 vCPU / 3.8 GiB 的 VPS，开着浏览器 profile
跑满整套栈：

| | 静息 | 铸造峰值 |
|---|---|---|
| `browser-rpc` | 234 MiB | **845 MiB** |
| `api` | 115 MiB | 128 MiB |
| `postgres` | 80 MiB | 88 MiB |
| `worker` / `redis` / `downloader` | 82 / 4 / 4 MiB | 基本不变 |
| **宿主机合计** | **1.0 GiB** | **1.1 GiB** |

决定这一档下限的是铸造峰值，不是静息值：Chromium 起一个上下文时会短时间冲高，
而那一刻 Postgres 正握着自己的缓冲区。**给这种机器加 swap**——2 GiB、
`vm.swappiness=10` 就够。它不是拿来用的，是为了让那个峰值变成慢一秒，
而不是内核挑一个进程杀掉（挑中的通常是 Postgres，而不是真正在涨的那个）。

### 怎么改这些上限

不要改 `docker/compose.yml`。它描述的是一台正常机器，你的机器和它的差异应该是
看得见的、单独一份文件。在仓库根目录建 `compose.host.yml`：

```yaml
services:
  postgres:    { mem_limit: 1g,    memswap_limit: 2g }
  redis:       { mem_limit: 320m,  memswap_limit: 512m }
  api:         { mem_limit: 448m,  memswap_limit: 768m,  cpus: 1.5 }
  worker:      { mem_limit: 448m,  memswap_limit: 768m }
  browser-rpc: { mem_limit: 1200m, memswap_limit: 2400m, cpus: 1.5 }
  downloader:  { mem_limit: 192m,  memswap_limit: 384m,  cpus: 1.0 }
```

然后每条命令都带上它：

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk \
  -f docker/compose.yml -f compose.host.yml --profile browser up -d
```

三个参数一个都不能少，忘一个就出怪事，所以值得包一个脚本。（[引导式脚本](#用脚本装和管) 会连同上面那份 `compose.host.yml` 一起生成这个文件，数值按你这台机器算。下面是它做了什么，以及你想自己写时怎么写。）

```bash
cat > dtkctl <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_ENV_FILES=.env
exec docker compose -p dtk -f docker/compose.yml -f compose.host.yml \
  --profile browser --profile downloader "$@"
EOF
chmod +x dtkctl
./dtkctl up -d
./dtkctl logs -f api
```

几个调参的方向，按效果排序：

- **`cpus` 给 `browser-rpc` 留少一点。** 签名和铸造都不在请求路径上，机器忙的时候
  该让路的正是它。给它 1.5 而不是 2.0，`api` 才有核可用。
- **`DTK_BROWSER_WARM_CONTEXTS`（默认 1）。** 每加一个常驻签名页大约多 300 MiB。
  小机器保持 1。
- **`DTK_DOWNLOADER_WORKERS` / `DTK_DOWNLOADER_ITEM_WORKERS`（默认各 4）。**
  并发传输数是两者相乘。1 核机器上降到 2 和 2。
- **`pool.target_size`（控制台里的设置）。** 身份池越大，铸造总次数越多、
  Postgres 里的行越多。小机器上 8 是个合适的数。

## 用脚本装和管

上面那条 Docker 路线，有一个引导式脚本可以替你走完，并且之后一直用它管这套实例。
它是中英双语的两份，见 [install/README.md](../../install/README.md)。

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh -o install.zh.sh
less install.zh.sh    # 先读一遍再跑
bash install.zh.sh
```

**第一次跑**：它识别发行版、检查 Docker（没有就问你要不要装）、按这台机器的核数和内存
算出合适的资源上限，然后问你六个问题 —— 装哪、绑什么地址端口、要不要浏览器容器、
要不要下载器、用已发布镜像还是源码构建。跑完它生成三样东西，正是本页后面教你手写的
那三样：

| 它生成的 | 本页对应的章节 |
|---|---|
| `.env`，密钥用 `openssl rand` 生成，权限 0600 | [首次安装](#首次安装) 第 2 步 |
| `compose.host.yml`，上限按本机缩放 | [怎么改这些上限](#怎么改这些上限) |
| `dtkctl`，带齐项目名、两个 compose 文件和 `COMPOSE_ENV_FILES` | 同上那一节 |

**再跑一次**：它发现已经装过了（位置是问 Docker 要的，装在哪都找得到），于是打开菜单
而不是重新安装：

- **状态** —— 版本、容器、磁盘占用，并和 GitHub 最新 Release 比一下
- **升级** —— 把 `DTK_IMAGE_TAG` 钉到目标版本，拉镜像、跑迁移、重启。迁移在切换之前跑，
  失败了旧容器还在，什么都没被换掉
- **管理** —— 改口令、加管理员、账号列表、备份、恢复、自检、日志、重启、改任意运行时设置、
  清理磁盘
- **停止或移除** —— 三档：只停、连数据卷一起删、再连安装目录一起删。后两档要求打出
  `delete` 这个词，不是 y/n

`bash install.zh.sh --manage` 可以直接进这个菜单。

**本页剩下的内容还是值得读。** 脚本做的就是下面这些事，只是替你做了；想知道它为什么
那样做、或者想偏离它的默认选择时，答案都在下面。


## 中国大陆的网络准备

这一节只对从中国大陆的机器上安装的人有用，其他地方跳过就好。

先说结论：**动手之前先把源换掉。** 这一路要拉的东西基本都在墙外——Docker Hub、PyPI、npm registry、GitHub、
Debian 的 apt 源——不换源不是慢一点的问题，而是十有八九在拉镜像那步超时，然后你会以为是项目坏了。

下面列的镜像站请当成"写这篇文档时能用"的线索，不是保证。公共镜像源这几年停了不少（2024 年就有一批高校站点关掉了
Docker Hub 代理），所以每节末尾都给了一条自己验证的办法。

### Docker 镜像

走推荐路径、只拉预构建镜像的话，需要从 Docker Hub 拉的是四个：

| 镜像 | 谁在用 |
|---|---|
| `evil0ctal/douyin_tiktok_download_api` | `api`、`worker`、`migrate` 共用同一个 |
| `evil0ctal/douyin_tiktok_download_api-downloader` | `downloader`（可选 profile） |
| `timescale/timescaledb-ha:pg17` | `postgres` |
| `redis:8-alpine` | `redis` |

加速器写在 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": ["https://<你的专属ID>.mirror.aliyuncs.com"]
}
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
docker info | grep -A3 "Registry Mirrors"   # 没打印出来就是没生效
```

几种来源，可靠性从高到低：

- **你所在云厂商自己的加速器。** 阿里云在容器镜像服务控制台给每个账号一个专属地址；腾讯云是
  `https://mirror.ccs.tencentyun.com`，但只在腾讯云机器的内网里能用；华为云、火山引擎同理。
  这类最稳，因为它是厂商发给自己客户的，不是公益站点。
- **公共加速器**，比如 `https://docker.m.daocloud.io`。能用就用，说停就停，`registry-mirrors`
  里可以一次写好几个，Docker 会依次试。
- **一个都用不了的时候**：在境外机器上 `docker pull`，然后 `docker save` / `docker load` 搬过来；
  或者在境外机器上跑一个 registry 代理。听着笨，但比反复试超时的加速器省时间。

**注意 `browser-rpc` 镜像不发布**，只能本地构建（见
[构建浏览器镜像：CloakBrowser 版本固定](#构建浏览器镜像cloakbrowser-版本固定)）。它的构建过程要访问 Debian
的 apt 源、PyPI、GitHub（CloakBrowser 是用 `pip install "cloakbrowser @ git+https://github.com/…"` 装的），
还要再下一个浏览器二进制。**这是整条链路上对网络最挑剔的一步**，下面几节多半是为它准备的。如果你暂时不需要自动铸造
身份，可以先不开 `browser` profile——导入自己的 Cookie 一样能跑。

### 系统软件源

[裸机部署](#在裸机上做生产部署) 那节要装 PostgreSQL 17、TimescaleDB 和 Redis，用的都是官方 apt 源。

Ubuntu 24.04 换源有个坑值得单说：源文件已经换成 deb822 格式，路径是 `/etc/apt/sources.list.d/ubuntu.sources`，
不是老教程里的 `/etc/apt/sources.list`。改后者不会有任何效果，也不会报错，只会让你以为换过了。

```bash
# Ubuntu 24.04，换成清华源
sudo sed -i \
  -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|http://ports.ubuntu.com/ubuntu-ports|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports|g' \
  /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update
```

第三条是给 arm64 机器的——ARM 版 Ubuntu 用的是 `ports.ubuntu.com` 而不是 `archive.ubuntu.com`，
在 x86 机器上那条不会匹配到任何东西，留着无害。

常用的几家，换掉域名就行：`mirrors.tuna.tsinghua.edu.cn`（清华）、`mirrors.ustc.edu.cn`（中科大）、
`mirrors.aliyun.com`（阿里云）、`repo.huaweicloud.com`（华为云）、`mirrors.cloud.tencent.com`（腾讯云）。
在哪家云的机器上就优先用哪家的，走内网既快又不计流量。

PostgreSQL 官方源（`apt.postgresql.org`）清华等站点有镜像，路径形如 `https://mirrors.tuna.tsinghua.edu.cn/postgresql/repos/apt/`——具体以镜像站自己的帮助页为准，这类路径偶尔会调整。

TimescaleDB 的包在 packagecloud.io 上，据我所知没有国内镜像。这一步卡住的话，要么给 apt 挂代理
（`sudo -E apt-get …` 配合 `https_proxy`），要么干脆走 Docker——`timescale/timescaledb-ha:pg17`
从加速器拉，比从 packagecloud 一个个装包快得多。这也是我推荐 Docker 的理由之一。

### Python、pip 和 uv

项目用 [uv](https://docs.astral.sh/uv/) 管依赖。

```bash
# uv 自己：官方安装脚本走 GitHub，慢就从 PyPI 装
pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple

# 依赖索引：uv 认这个环境变量
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
uv sync --frozen --no-dev
```

写进 `~/.bashrc`，或者写进 systemd unit 的 `Environment=` 里更省事。老版本 uv 认的是 `UV_INDEX_URL`，
两个都设上没有坏处。

pip 自己：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

常用的 PyPI 镜像：清华 `https://pypi.tuna.tsinghua.edu.cn/simple`、阿里云
`https://mirrors.aliyun.com/pypi/simple/`、中科大 `https://mirrors.ustc.edu.cn/pypi/simple`、腾讯云
`https://mirrors.cloud.tencent.com/pypi/simple`、华为云 `https://repo.huaweicloud.com/repository/pypi/simple`。

还有一个容易漏的：`uv python install` 下载的解释器来自 GitHub Releases，慢的话设 `UV_PYTHON_INSTALL_MIRROR`；
或者直接用系统自带的 Python——本项目要求 `>=3.12,<3.14`，Ubuntu 24.04 自带的 3.12 就满足。

### Node 和 npm

只有你要自己构建控制台、或者自己构建 app 镜像时才用得上；拉预构建镜像不需要 Node。

```bash
npm config set registry https://registry.npmmirror.com
```

`npm ci` 会拉 esbuild、rollup 这类带原生二进制的包。它们在现在的版本里是以平台专属的 npm 包发布的，
所以换 registry 就够了，不用再单独配二进制镜像地址。

顺带一提，`downloader` 那个 Go 服务**不需要** `GOPROXY`：它一个第三方依赖都没有，只用标准库，
构建时不会去拉任何模块。

### GitHub 与构建时代理

`git clone` 和 CloakBrowser 的 `pip install "… @ git+https://github.com/…"` 都要访问 GitHub。
最省事的是给 git 配一个只对 GitHub 生效的代理，而不是把全局都代理掉：

```bash
git config --global http.https://github.com.proxy http://127.0.0.1:7890
```

Docker 构建里的网络访问不走宿主机的 git 配置，代理要通过 build 参数传进去（`HTTP_PROXY` /
`HTTPS_PROXY` 是 BuildKit 的预定义参数，Dockerfile 里不用声明）：

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml build \
  --build-arg HTTP_PROXY=http://172.17.0.1:7890 \
  --build-arg HTTPS_PROXY=http://172.17.0.1:7890 \
  browser-rpc
```

`172.17.0.1` 是默认 bridge 网络里宿主机的地址。这里有个常见的坑：如果你的代理只监听 `127.0.0.1`，
容器里是连不上它的——得让它监听 `0.0.0.0`，或者至少监听 docker0 那个地址。

### 换完之后确认一遍

别靠感觉，跑一遍：

```bash
docker info | grep -A5 "Registry Mirrors"    # 加速器读到了吗
time docker pull redis:8-alpine              # 快不快，一试便知
pip config get global.index-url
npm config get registry
```

如果换完源某一步还是失败，先把那一步单独跑一遍看它自己的报错，再来怀疑项目——这一节列的每样东西，
失败时都会说出是哪个域名连不上。

## 整体结构一览

| 服务 | 镜像 | profile | 可选 | 干什么 |
|---|---|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg17` | 默认 | 否 | 所有持久化的行：身份、代理、任务、归档、请求日志、设置 |
| `redis` | `redis:8-alpine` | 默认 | 否 | 任务队列、令牌桶、限流计数器、响应缓存、控制台会话 |
| `migrate` | `dtk-app` | 默认 | 否 | 一次性任务：执行迁移后退出，其余服务都等它 |
| `api` | `dtk-app` | 默认 | 否 | REST API、MCP、控制台、`/healthz` 和 `/readyz`；唯一对外发布端口的服务 |
| `worker` | `dtk-app` | 默认 | 否 | 领取任务，驱动调度器和身份池，跑后台作业 |
| `browser-rpc` | `dtk-browser-rpc` | `browser` | **是** | 无头浏览器：铸造游客身份，以及可选的签名兜底 |
| `downloader` | `dtk-downloader` | `downloader` | **是** | 把媒体文件写到数据卷上的 Go sidecar |

两个网络：

- **`data`** 声明为 `internal: true`。`postgres` 和 `redis` 挂在上面，完全没有出宿主机的路由——既到不了
  互联网，也到不了这个 compose 项目之外的任何东西。
- **`edge`** 是普通的桥接网络。`api`、`worker`、`browser-rpc` 和 `downloader` 在上面，因为它们需要出网。
  `browser-rpc` 和 `downloader` **只**在 `edge` 上：两者都没有理由访问数据库，所以它们也访问不到。

## 首次安装

**1. 把仓库拉下来。** compose 文件、Dockerfile 和迁移脚本都在里面，默认分支就是 v5。下面的命令
一律在仓库根目录执行：

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

拿到的是不是对的代码树，看一眼这个文件就知道——它不存在说明你在 `v4` 分支上，本页一条都不适用：

```bash
ls docker/compose.yml
```

**2. 写 `.env`。** 两个密码用 hex 而不是 base64，因为它们最终会出现在连接 URL 里，而 `+` 和 `/` 在那里需要转义：

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
cat > .env <<EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
DTK_DATABASE_URL=postgresql+asyncpg://dtk:${POSTGRES_PASSWORD}@postgres:5432/dtk
DTK_REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
EOF
```

仓库根目录的 `.env.example` 列出了引导变量，大多数带注释（`DTK_BACKUP_DIR` 不在其中，另有几个也没有
注释），想从模板起步就照着抄。其中有四行——`DTK_BIND_HOST`、`DTK_BIND_PORT`、`DTK_REDIS_MAXMEMORY` 和
`CLOAKBROWSER_COMMIT`——是由 Compose 插值读取的，所以只有当这份文件同时喂给 Compose（放成 `docker/.env`、
从 shell 传入，或者经由 `COMPOSE_ENV_FILES`）时才会生效。后两个除此之外没有任何读取方；前两个同时也是应用
变量，但 `api` 容器在自己的 `environment:` 里钉死了监听地址，所以这份文件里的这两行要么经由插值改变发布
地址，要么什么都不改。见下文 [Compose 读取 `.env` 的两种方式](#compose-读取-env-的两种方式)。
`.env` 已被 git 忽略，也在 `.dockerignore` 里排除，所以它是以 env 文件的形式送进容器的，从不会成为镜像内容。

**3. 把整套栈拉起来。** 第一次会构建 `dtk-app` 镜像，其中包含一次 `npm ci` 和一次 `uv sync`，需要几分钟：

```bash
docker compose -p dtk -f docker/compose.yml up -d --wait
```

`--wait` 只有在每个服务都报告健康之后才返回。`api` 的健康检查是就绪探针，所以 `--wait` 成功返回意味着
API 真的能处理请求了。

**4. 从日志里读出初始化令牌：**

```bash
docker compose -p dtk -f docker/compose.yml logs api
```

API 会打印一段横幅，里面是形如 `http://127.0.0.1:8000/setup?token=…` 的地址。打开它，创建第一个管理员。
令牌存在 Redis 里，有效期 24 小时，重启时是复用而不是替换（所以你手上那条链接仍然有效），第五次失败尝试
就会把它作废——前四次错还有救。一旦账号建立，令牌被删除、`/setup` 关闭。若实例仍然没有账号、而你想重新
拿到那条链接，重启 api 容器即可——它会把同一个令牌再打印一遍。只有在旧令牌过期、或被五次失败尝试作废之后，
才会签发新的。

整个过程不需要编辑任何配置文件。除引导变量之外的一切都存在数据库里、在控制台中修改——见
[配置参考](./03-configuration.md)。

## compose 文件逐个服务讲解

### postgres

`timescale/timescaledb-ha:pg17`，只挂在 `data` 网络上。它会创建数据库 `dtk`，属主是用户 `dtk`，认证方式
`POSTGRES_HOST_AUTH_METHOD=scram-sha-256`；密码来自 `.env` 里的 `POSTGRES_PASSWORD`。它的
`environment:` 块把 `DTK_SECRET_KEY` 置空——数据库用不到那把加密它自身内容的密钥。

它的数据目录是 `/home/postgres/pgdata/data`，不是常见的 `/var/lib/postgresql/data`。这是
`timescaledb-ha` 镜像自己的布局；挂常规路径会把真正的数据目录留在容器里，第一次重建就丢库。如果你要换镜像，
先确认这个路径。

健康检查：`pg_isready -U dtk -d dtk`，每 10s 一次，重试 12 次，启动宽限 30s。`stop_grace_period` 给了
整整一分钟，免得 checkpoint 被中途打断。

### redis

`redis:8-alpine`，只挂在 `data` 网络上，以 `redis` 用户运行（镜像自带的 entrypoint 只有在第一个参数字面上
是 `redis-server` 时才会降权，而这个服务传的是一段 shell 命令，所以降权在 compose 文件里做）。

它承载任务队列、调度器的令牌桶、限流计数器、响应缓存、控制台会话和初始化令牌。持久化是
`appendonly yes` 加 `save 60 1`。

有两项配置很关键：

- `--maxmemory ${DTK_REDIS_MAXMEMORY:-320mb}`，明显低于容器的 512m 上限。响应缓存对每个不同的请求写一条，
  保留 `cache.content_ttl`（默认 30 分钟）；实测单条 16–200 KB，所以繁忙实例可能在一个 TTL 窗口内塞进
  几百 MB。没有上限的话，结局是整个容器（连同队列）被 OOM 杀掉，而不是一次缓存未命中。
- `--maxmemory-policy volatile-lru`，不是 `allkeys-lru`。队列列表没有 TTL，因此永远不会成为淘汰候选；
  可被淘汰的都是带 TTL 的：缓存条目、限流计数器、令牌桶、会话——它们都可以重建。排队中的任务不行。

`REDIS_PASSWORD` 为空时容器拒绝启动；它的健康检查也和应用一样带认证，而不是满足于一次未认证的 `PING`。

### migrate

和 `api`、`worker` 同一个 `dtk-app` 镜像，只是启动参数是 `migrate`。它把 Alembic 迁移执行到 `head` 然后
退出；`restart: "no"`，没有健康检查，因为一次性容器的退出码**就是**它的健康状态——这正是 `api` 和 `worker`
上的 `service_completed_successfully` 读取的东西。

它作为独立服务存在，而不是让 `api` 和 `worker` 各自尝试一次，是因为并发的 `CREATE TABLE` 竞争正是首次启动
把自己搞坏的方式，也因为没人应该记得“拉完代码要跑迁移”。迁移是幂等的，所以它每次启动都会跑。

`DTK_SECRET_KEY` 这道门在这里同样有效：为一个解不开自己凭据的部署建好表结构，比一次失败的迁移更糟；这样一来，
失败发生在任何表被创建之前。

### api

`dtk-app` 镜像，参数 `api`，启动的是 `uvicorn dtk.api.app:create_app --factory --no-server-header`。
它对外提供：

| 路径 | 是什么 |
|---|---|
| `/` | React 控制台（单页应用；未知路径回退到 `index.html`） |
| `/api/v1/…` | REST API |
| `/api/setup/…` | 首次初始化，账号建立后关闭 |
| `/mcp/` | MCP 端点。末尾的斜杠要保留：`/mcp` 会以 `307 Temporary Redirect` 跳到它——见 [MCP 与 AI 客户端](./12-mcp.md) |
| `/docs` | 控制台内置的 API 参考页 |
| `/swagger`、`/redoc`、`/openapi.json` | 生成的 API 文档，无需登录 |
| `/healthz` | 存活探针。刻意不碰任何依赖 |
| `/readyz` | 就绪探针：探测 Postgres 和 Redis，任一挂掉返回 `503` |

它自己不发起任何上游平台请求——它做的是认证、校验、把活交给 service 层，然后拼装回复。这就是为什么它是唯一
发布端口、也是唯一需要被访问到的容器。

它的 `environment:` 块里钉死了五个变量，因此无法从 `.env` 覆盖（`environment:` 优先于 `env_file:`）：
`DTK_BIND_HOST=0.0.0.0`、`DTK_BIND_PORT=8000`、`DTK_CONSOLE_DIR=/app/web/dist`、
`DTK_BACKUP_DIR=/var/lib/dtk/backups` 和 `DTK_MEDIA_DIR=/var/lib/dtk/media`。真正起作用的只有前四个。
没有任何代码读取 `DTK_MEDIA_DIR`——媒体路径是 `dtk.ops.capacity` 里的常量 `MEDIA_PATH`——所以 compose
文件设了它、却没有任何读取方，这也是下文各张变量表里都没有它的原因。这里的监听地址是容器自己的；限制
暴露面的是发布地址，不是这个值。

数据卷：`backup-data` 以读写方式挂在 `/var/lib/dtk/backups`（它列出并提供 worker 写的归档），`media-data`
以**只读**方式挂在 `/var/lib/dtk/media`。只读是刻意的：控制台需要把已存的文件递给浏览器，而 API 是这套系统
里唯一有认证、权限范围、限流和审计记录的部分——所以由它来吐字节，downloader 保持只写不转发。

健康检查：`GET /readyz`，每 15s 一次，重试 5 次，启动宽限 40s。`browser-rpc` 刻意不进入就绪判断——铸造不在
请求路径上，所以没有它的实例是降级，不是未就绪。

### worker

同一个镜像，参数 `worker`，实际运行 `python -m dtk.worker`。它从 Redis 队列领取任务，经调度器和身份池执行，
写回结果；同时还跑身份池补充、代理探测、数据保留、关注列表 tick 和归档复查。

一个 worker 同时跑 4 个任务。真正的并发上限来自身份池而不是这个数字——多出来的槽位只是在调度器那里排队。

它没有端口，所以健康检查是向 `DTK_REDIS_URL` 里的地址开一个 TCP 连接：连不上 Redis 的 worker 什么都干不了，
而那正是值得检测的故障。

`stop_grace_period` 是 45s，足够让一次进行中的上游请求完成并释放身份租约，而不是留着等 TTL 过期。请求做到
一半被杀掉，浪费的是一个身份的配额。

### browser-rpc（profile `browser`）

一个包着无头浏览器的小型 FastAPI 服务，两项职责都不在热路径上：通过身份自己的代理铸造游客身份；以及在进程内
算法失效时，用平台自己的 JavaScript 来签名。它的 RPC 接口是 `POST /rpc/mint`、`POST /rpc/sign` 和
`GET /rpc/health`。

它是整套栈里最重的东西，也是资源上限存在的原因。热态实测 2.57 GiB、629% CPU——实打实用掉六个核的 Chromium。
4 核的上限是刻意压在它之下的：签名和铸造都不在请求路径上，所以机器忙的时候，该让路的正是这个服务。

它写的一切都在内存里：

| 挂载点 | 大小 | 为什么 |
|---|---|---|
| `/tmp` | tmpfs，3g | 驱动自己传了 `--disable-dev-shm-usage`，于是每个渲染进程的共享内存都落在这里。每个常驻上下文约 300 MB；1g 勉强放下三个，开第四个时 Chromium 直接崩溃，而那个报错读起来和平台风控一模一样 |
| `/profiles` | tmpfs，1g | 一次性的铸造 profile。用 tmpfs 让“绝不复用、绝不落盘”成为结构性事实，而不是对清理代码的承诺 |
| `shm_size` | 1gb | 给将来某个不再禁用 `/dev/shm` 的构建留的保险，它不是渲染进程崩溃的解法 |

如果你调高 `DTK_BROWSER_WARM_CONTEXTS`，请同时调大 `docker/compose.yml` 里 `/tmp` 的 tmpfs：按
`300M × warm_contexts × 2 个平台` 估算，再加上一次进行中的铸造。

它丢掉全部 capability，只加回 `SYS_ADMIN`——Chromium 自己的沙箱需要它来创建 user namespace。相比
`--no-sandbox`（那会彻底取消渲染进程边界），这是两害相权取其轻。`init: true` 是因为 Chromium 会留下僵尸进程，
PID 1 必须去回收。

它的健康检查读的是响应体里的 `status` 字段，而不只是状态码：即使浏览器后端启动失败，`/rpc/health` 也照样返回
`200`——这是刻意的，为的是让你在日志里拿到原因而不是陷入重启循环——而只看状态码的探针会把一个没装浏览器的镜像
判成健康。

### downloader（profile `downloader`）

一个基于 `scratch` 的静态 Go 二进制：没有 shell，没有包管理器，除了这个二进制没有别的东西可执行。它是唯一
会去连任意 CDN 主机、并把它们的字节写到你在乎的磁盘上的容器，所以它里面的东西最少。它的健康检查是二进制自己
探自己（`downloader healthcheck`），因为 scratch 镜像里没有 `curl`，而为了加一个 `curl` 就会毁掉这个镜像
用 scratch 的理由。

它以读写方式挂载 `media-data` 到 `/var/lib/dtk/media`，且只在 `edge` 网络上。尽管共享的 env 文件里带着
`DTK_SECRET_KEY`、`DTK_DATABASE_URL` 和 `DTK_REDIS_URL`，它的 `environment:` 块把这三个全部置空：这是
一旦被攻破后果最严重的组件，也是最不需要这些东西的组件。

文件按 `<platform>/<author>/<content id>/` 落盘，旁边放一份 `meta.json`。传输用 `io.Copy` 流式完成，所以
常驻内存是每个并发文件几个 32 KiB 缓冲区加上 Go 运行时——和文件大小无关。

## 两个可选 profile

两个 profile 默认都不启动。不开其中任何一个都是受支持的部署形态，不是残缺状态。

**browser profile** 给你自动身份铸造和浏览器签名兜底：

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

需要同时满足两件事：容器要跑起来（profile），并且要告诉 `api` 和 `worker` 它在哪（`.env` 里的
`DTK_BROWSER_RPC_URL`）。不设的话，它们就只走手动导入——你从浏览器里把 cookie 粘进来，身份池照此工作。这是
正确的降级模式而不是错误状态，而且当 `browser-rpc` 不可达时，身份池会自己退回到这条路。参见
[身份与代理](./06-identities-and-proxies.md)。

**downloader profile** 让媒体存到你自己的磁盘上：

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

形式一样：profile 加上 `DTK_DOWNLOADER_URL`。不设的话，`POST /api/v1/downloads` 返回 `501`，并附上一条
说明怎么打开它的消息，其他一切照旧——解析、归档和关注列表都不受影响。已存媒体的总量由 `media.max_bytes`
设置限制，默认 2 GiB；超出后最旧的未固定下载会被删除，并有一条告警说明删了什么。参见
[下载、资料库与关注列表](./08-downloads-and-library.md)。

同时开两个：

```bash
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser --profile downloader up -d --build
```

`build`、`config` 和 `down` 需要重复这些 profile 参数：这几条命令读的是 compose 文件而不是正在运行的栈，
不带参数 Compose 就不知道这些服务存在——而不带参数的 `down` 会停掉核心服务、把 profile 里的容器留在原地
继续跑。`ps` 和 `logs <服务名>` 靠项目标签找到正在运行的容器，不需要这些参数。嫌麻烦就在 shell 里
`export COMPOSE_PROFILES=browser,downloader`。

## 构建浏览器镜像：CloakBrowser 版本固定

GitHub 上至少有三个组织发布着名叫 `cloakbrowser`、描述完全相同的仓库。所以“最新的 cloakbrowser”并不指向
任何一份确定的软件；镜像因此同时接受**一个仓库和一个 commit**，并把这一对烙进 `DTK_BROWSER_BACKEND_PIN`，
由 `/rpc/health` 报告出来。“这到底是哪个浏览器”这个问题，可以从运行中的系统回答，而不是靠翻构建历史。

```bash
CLOAKBROWSER_REPO=https://github.com/CloakHQ/cloakbrowser \
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser build browser-rpc
```

`.env.example` 里给了一个已知可用的起点：
`CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce`（CloakBrowser 0.5.10，Chromium 146，
2026-09-08 端到端验证过）。要改就有意识地改。

**这两个是构建参数，由 Compose 插值解析，所以仓库根目录的 `.env` 是放错了地方。** 按上面那样写在 shell 里，
或者写进 `docker/.env`，再或者用 `COMPOSE_ENV_FILES=.env` 执行构建，让 Compose 从根目录那份文件取值。
下一节解释原因。

不带 commit 构建出来的镜像里仍然有服务本身和 `fake` 后端，但没有 Chromium。此时除非你明确设置
`DTK_BROWSER_BACKEND=fake`，它会拒绝铸造。没有任何东西会自己退回 fake 后端：一个悄悄被合成身份填满的
身份池看起来非常健康，直到每一个请求都返回风控为止。

如果你想固定到一个已经带浏览器的上游镜像，而不是从源码构建，就传一个 digest：

```bash
docker build -f docker/Dockerfile.browser \
  --build-arg "BROWSER_BASE_IMAGE=ghcr.io/<org>/cloakbrowser@sha256:<digest>" \
  --build-arg "CLOAKBROWSER_COMMIT=<sha>" -t dtk-browser-rpc:dev .
```

构建参数及 Dockerfile 里声明的默认值：

| 镜像 | 参数 | 默认值 | 含义 |
|---|---|---|---|
| `Dockerfile` | `PYTHON_VERSION` | `3.12.8`（compose 传的是同一个值） | 基础 Python；和 `pyproject.toml` 里的运行时版本一起升 |
| `Dockerfile` | `NODE_VERSION` | `22` | 只用于构建控制台的那个阶段，它不会进入运行时镜像 |
| `Dockerfile` | `UV_VERSION` | `0.9.18` | 固定版本，因为对着 `uv.lock` 跑 `uv sync --frozen` 才是可复现性的保证 |
| `Dockerfile` | `CONSOLE_OUT_DIR` | `dist` | 用 `test -d` 校验，于是“Vite 配置把产物写到别处”变成构建失败，而不是发布一个什么都不提供的镜像 |
| `Dockerfile`、`Dockerfile.browser` | `DTK_UID` / `DTK_GID` | `10001` | 非特权运行用户 |
| `Dockerfile.browser` | `BROWSER_BASE_IMAGE` | `python:${PYTHON_VERSION}-slim-bookworm` | 可换成已带 CloakBrowser 的 digest 固定镜像 |
| `Dockerfile.browser` | `CLOAKBROWSER_REPO` | `https://github.com/CloakHQ/cloakbrowser` | 从哪个仓库安装 |
| `Dockerfile.browser` | `CLOAKBROWSER_COMMIT` | *(空)* | 版本号。空的意味着镜像里没有浏览器 |
| `Dockerfile.browser` | `CLOAKBROWSER_INSTALL_CMD` | `python -m cloakbrowser install` | 拉取浏览器二进制的命令；它属于你固定的那个版本，所以换 commit 时要顺手核对 |
| `Dockerfile.browser` | `CLOAKBROWSER_HOME` | `/opt/cloakbrowser` | 浏览器装在哪。运行时根文件系统只读，所以 entrypoint 会把它软链到容器的 `HOME` 下 |
| `Dockerfile.downloader` | `VERSION` | `dev`（compose 传 `${DTK_IMAGE_TAG:-dev}`） | 烙进二进制里的版本号 |

## 环境变量

### compose 读取 `.env` 的两种方式

这一点几乎所有人都会踩，所以值得说明白。

- **服务变量**来自 `env_file: ../.env`，Compose 解析这个路径时是相对于 compose 文件的。compose 的项目目录是
  `docker/`，所以 `../.env` 指的是**仓库根目录**的 `.env`。文件不存在也不算错误（`required: false`）——
  由 entrypoint 给出那条有用的提示。
- **插值**——也就是 `compose.yml` 内部的 `${…}` 表达式——**不读**那份文件。它读的是 `docker/.env` 或你的
  shell。一共涉及六个变量、出现在九处表达式里：`DTK_BIND_HOST`、`DTK_BIND_PORT`（发布地址）、
  `DTK_IMAGE_TAG`（镜像标签，出现在四处）、`DTK_REDIS_MAXMEMORY`（redis 上限），以及
  `CLOAKBROWSER_REPO` / `CLOAKBROWSER_COMMIT`（构建参数）。

所以这样是有效的：

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

明确让 Compose 去读根目录那份文件也有效：

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

但把 `DTK_BIND_HOST=0.0.0.0` 写进根目录 `.env` 然后指望端口跟着变，是无效的。你随时可以在不启动任何东西的
情况下查看 Compose 解析出的结果：

```bash
docker compose -p dtk -f docker/compose.yml config
```

在这之上还有一条规则：`environment:` 压过 `env_file:`。`api` 在自己的 `environment:` 块里把
`DTK_BIND_HOST` 和 `DTK_BIND_PORT` 钉在 `0.0.0.0:8000`，所以这两个值写在根目录 `.env` 里对 compose 栈内部
没有任何影响。它们仍然决定 Docker 之外 `dtk serve` 监听在哪。

### 引导配置变量

这些在进程启动时读一次，来自环境变量，因为它们在数据库可用之前就需要。改动其中任何一个都要重启。它们就是
`src/dtk/core/config.py` 里 `BootstrapSettings` 的全部内容，统一带 `DTK_` 前缀。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DTK_SECRET_KEY` | *(无——必填)* | 加密所有已存 cookie 和代理凭据的主密钥。至少 32 个字符，用 `openssl rand -base64 48` 生成。改掉它会让已存凭据无法解密 |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | 数据库。驱动必须是 `asyncpg` |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | 队列、缓存和计数器 |
| `DTK_BIND_HOST` | `127.0.0.1` | 监听地址。在 api 容器内被钉为 `0.0.0.0` |
| `DTK_BIND_PORT` | `8000` | 监听端口。在 api 容器内被钉为 `8000` |
| `DTK_LOG_LEVEL` | `info` | 日志级别 |
| `DTK_LOG_JSON` | `true` | 结构化 JSON 日志。排查问题时可以设成 `false` 以获得人类可读的输出 |
| `DTK_BROWSER_RPC_URL` | *(空)* | `browser-rpc` 在哪。空表示关闭自动铸造，身份池转而依赖手动导入 |
| `DTK_BACKUP_DIR` | `backups` | 备份归档写入和列举的目录。镜像里被设为 `/var/lib/dtk/backups`；相对路径的默认值适合源码检出 |
| `DTK_DOWNLOADER_URL` | *(空)* | downloader sidecar 在哪。空表示完全关闭媒体下载 |
| `DTK_DOWNLOADER_TOKEN` | *(空)* | 该 sidecar 的可选共享密钥。空表示 sidecar 不做校验。两侧必须一致 |

### 引导配置之外读取的变量

| 变量 | 默认值 | 由谁读取 | 作用 |
|---|---|---|---|
| `DTK_FORWARDED_ALLOW_IPS` | *(空)* | `docker/entrypoint.sh` 和 `dtk.api.routes.support` | 你的反向代理发起连接所用的地址，逗号分隔。设了之后 entrypoint 会以 `--proxy-headers --forwarded-allow-ips` 启动 uvicorn。应用本身也会读它，用来判断是否启用按地址计的登录失败上限——并且把 `*` 当作根本没有声明，因为一个谁都能伪造的地址，比一个被共用的地址更不适合做封锁依据 |
| `DTK_WORKER_COMMAND` | *(空)* | `docker/entrypoint.sh` | 完全覆盖 worker 的命令行。只有当 worker 模块在两个常用名字下都无法导入时才需要 |
| `DTK_CONSOLE_DIR` | 镜像里是 `/app/web/dist` | `dtk.api.console` | 构建好的控制台在哪。有回退逻辑，所以源码检出下不设也能工作 |
| `DTK_COMMIT`、`DTK_GIT_COMMIT`、`GIT_COMMIT` | *(空)* | `dtk.ops.health` | 控制台系统页上显示的构建来源。默认没有任何东西会设置它们 |

### compose 层面的变量

由 Compose 插值读取，所以它们该放在你的 shell 或 `docker/.env` 里。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DTK_BIND_HOST` | `127.0.0.1` | api 端口发布到宿主机的哪个地址 |
| `DTK_BIND_PORT` | `8000` | api 发布到宿主机的哪个端口 |
| `DTK_IMAGE_TAG` | `dev` | `dtk-app`、`dtk-browser-rpc` 和 `dtk-downloader` 的标签 |
| `DTK_REDIS_MAXMEMORY` | `320mb` | Redis 的 `maxmemory`。保持明显低于容器的 512m 上限 |
| `CLOAKBROWSER_REPO` | `https://github.com/CloakHQ/cloakbrowser` | 浏览器镜像的构建参数 |
| `CLOAKBROWSER_COMMIT` | *(空)* | 浏览器镜像的构建参数 |

另外还有两个容器直接需要、确实住在根目录 `.env` 里的变量：

| 变量 | 作用 |
|---|---|
| `POSTGRES_PASSWORD` | postgres 容器创建 `dtk` 用户时使用的密码。必须和 `DTK_DATABASE_URL` 里带的一致 |
| `REDIS_PASSWORD` | redis 容器启动时使用的密码，也是它健康检查所用的认证。必须和 `DTK_REDIS_URL` 里带的一致 |

让它们和 URL 不同步是一种静默失败：容器照常启动，只有连接会失败。“首次安装”里的写法之所以用同一个 shell 变量
写两处，正是为了这个。

### browser-rpc 的变量

全部以 `DTK_BROWSER_` 为前缀，全部在启动时读一次，全部可以写在仓库根目录的 `.env` 里——浏览器容器和其余服务
共用同一个 `env_file`。其中被容器自己钉死的三个（`BIND_HOST`、`BIND_PORT`、`PROFILE_ROOT`）必须和
`expose`、健康检查以及 tmpfs 对齐，所以它们写在 `environment:` 里且无法覆盖。

| 变量 | 默认值 | 含义 |
|---|---|---|
| `DTK_BROWSER_BACKEND` | `cloak` | `cloak` 或 `fake`。两者之间没有任何回退 |
| `DTK_BROWSER_BACKEND_PIN` | 由镜像设置 | 仓库和 commit，由 `/rpc/health` 报告 |
| `DTK_BROWSER_BIND_HOST` | `127.0.0.1`（compose 钉为 `0.0.0.0`） | 监听地址 |
| `DTK_BROWSER_BIND_PORT` | `9000` | 监听端口。从不发布到宿主机 |
| `DTK_BROWSER_PROFILE_ROOT` | `/tmp/dtk-browser-profiles`（compose 钉为 `/profiles`） | 一次性铸造 profile 的父目录 |
| `DTK_BROWSER_WARM_CONTEXTS` | `1` | 每个平台常驻的热签名上下文数量 |
| `DTK_BROWSER_WARM_REFRESH_SECONDS` | `1800` | 热页面重建的年龄阈值；平台会频繁更新 JavaScript |
| `DTK_BROWSER_PREWARM` | `true` | 启动时就建好热页面，而不是等第一次调用 |
| `DTK_BROWSER_MAX_CONCURRENT_MINTS` | `2` | 同时铸造的浏览器数量（每个约 500 MB） |
| `DTK_BROWSER_MINT_TIMEOUT_SECONDS` | `75` | 服务端铸造预算，低于客户端的 90s |
| `DTK_BROWSER_SIGN_TIMEOUT_SECONDS` | `8` | 服务端签名预算，低于调用方的上限（`signing.rpc_timeout_seconds`，默认 60） |
| `DTK_BROWSER_CONTEXT_OPEN_TIMEOUT_SECONDS` | `45` | 打开一个浏览器上下文的预算 |
| `DTK_BROWSER_SDK_READY_TIMEOUT_SECONDS` | `25` | 新页面在导航结束后还需要多久才具备签名能力。设得太低会得到读起来像“算法变了”、实际只是等不及的失败 |
| `DTK_BROWSER_SIGN_PROXY_URL` | *(未设)* | 热签名页面的可选出口。不设的话它们会从宿主机自己的地址加载平台站点；铸造不受影响，它始终走身份自己的代理 |
| `DTK_BROWSER_GEO_PROBE_URL` | `https://ipinfo.io/json` | 在创建上下文之前，**通过代理**查询以确定出口国家的回显端点。留空则禁用 |
| `DTK_BROWSER_GEO_PROBE_TIMEOUT_SECONDS` | `8` | 该探测的预算。它从不导致铸造失败 |
| `DTK_BROWSER_DEFAULT_COUNTRY` | `US` | 调用方和探测都不知道时使用的两位国家码 |
| `DTK_BROWSER_HEADLESS` | `true` | 只有在带显示器的机器上调试时才关掉 |
| `DTK_BROWSER_LOG_LEVEL` | `info` | 该服务的日志级别 |

### downloader 的变量

全部以 `DTK_DOWNLOADER_` 为前缀，由 Go 二进制在启动时读一次，可以写在根目录 `.env` 里。`BIND` 和 `ROOT`
被 compose 钉死，因为它们必须和 `expose`、健康检查以及卷挂载对齐。

| 变量 | 默认值 | 含义 |
|---|---|---|
| `DTK_DOWNLOADER_BIND` | `0.0.0.0:9100` | 监听地址（由 compose 钉死） |
| `DTK_DOWNLOADER_ROOT` | `/var/lib/dtk/media` | 文件写到哪（由 compose 钉死） |
| `DTK_DOWNLOADER_TOKEN` | *(空)* | 共享密钥；必须和 api 侧的 `DTK_DOWNLOADER_TOKEN` 一致 |
| `DTK_DOWNLOADER_WORKERS` | `4` | 并行的作业数 |
| `DTK_DOWNLOADER_ITEM_WORKERS` | `4` | 单个作业内并行的文件数。并发传输数是这两者的乘积 |
| `DTK_DOWNLOADER_QUEUE` | `64` | 队列深度 |
| `DTK_DOWNLOADER_HISTORY` | `500` | 内存中保留供状态查询的已完成作业数 |
| `DTK_DOWNLOADER_MAX_REDIRECTS` | `5` | 单次传输的重定向上限 |
| `DTK_DOWNLOADER_TIMEOUT_SECONDS` | `900` | 单次传输的时间上限 |

值为空、无法解析或不为正数时，会回退到默认值而不是导致启动失败。

### 从环境变量播种的运行时设置

其余一切都是运行时设置：它们存在 `settings` 表里，可以在控制台中修改且无需重启，并且**只在首次初始化时**从
环境变量播种一次。环境变量名是设置键大写、点换成下划线、前面加 `DTK_`，所以 `cache.content_ttl` 由
`DTK_CACHE_CONTENT_TTL` 播种。

首次初始化之后，环境变量就不再优先了。如果你改了 `.env` 里的这类值却毫无反应，原因就在这里——请改到控制台里去。
完整清单见 [配置参考](./03-configuration.md)。

## 数据卷

四个命名卷，全部由 Docker 管理。用命名卷而不是 bind mount，是为了让默认安装不可能往你的检出目录里写东西。

| 卷 | 挂载点 | 谁在写 | 里面是什么 |
|---|---|---|---|
| `postgres-data` | `postgres` 的 `/home/postgres/pgdata/data` | postgres | 所有持久化数据：身份、代理、用户、API 密钥、任务、内容归档、请求日志、设置 |
| `redis-data` | `redis` 的 `/data` | redis | AOF 与 RDB 快照：队列能挺过重启，缓存则不需要 |
| `backup-data` | `api` 和 `worker` 的 `/var/lib/dtk/backups` | worker 写，api 读 | 备份归档。之所以共享，是因为 worker 写、API 列出并提供下载 |
| `media-data` | `downloader`（读写）和 `api`（只读）的 `/var/lib/dtk/media` | downloader | 下载的媒体，按 `<platform>/<author>/<content id>/` 布局，旁边有 `meta.json` |

`backup-data` 和 `media-data` 之所以存在，是因为应用容器的根文件系统是只读的——它们唯一另外可写的地方是
`/tmp` 上一个 64 MB 的 tmpfs。备份目录在镜像里就创建好并归属运行用户，因为 Docker 会用镜像在挂载点上的内容
（包括属主）来初始化一个新建的命名卷：目录不存在或属于 root，卷就会归 root 所有，非 root 进程写不了，而错误
会在有人要备份的那一刻才出现，而不是在启动时。downloader 镜像用一个空的 `/seed/media` 做了同样的事。

查看它们叫什么、占了多少：

```bash
docker volume ls --filter label=com.docker.compose.project=dtk
docker system df -v | grep dtk_
```

## 端口与监听地址

只有 `api` 发布端口，而且默认只发布到回环地址：

```yaml
ports:
  - "${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000"
```

`browser-rpc` 和 `downloader` 用的是 `expose`，它不向宿主机发布任何东西——只有 `edge` 网络上的其他容器能访问
它们。`postgres` 和 `redis` 什么都不发布，并且待在一个完全没有出宿主机路由的内部网络上。这是 compose 文件为
你的安全姿态做的最重要的一件事，也是最容易被不小心撤销的一件事。

对外提供服务被设计成一个明确的动作：

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

只有在前面挂了 TLS 的情况下才这么做。容器不终结 TLS，也不会替你做。如果你的反向代理跑在同一台机器上，就保持
默认的回环绑定，让代理指向 `127.0.0.1:8000`；根本不需要往公网接口上发布任何端口。

## 放在反向代理后面

代理往 API 前面一站，立刻会冒出两个问题，两个都有确切的答案。

**来源地址。** 经过 TLS 终结器的一切都带着终结器的地址，经过 Docker 发布端口的用户态代理的一切都带着网桥
网关的地址。无论哪种，`request.client.host` 都是全互联网共用的一个地址。`DTK_FORWARDED_ALLOW_IPS` 就是你
告诉它“不是这样”的方式：

```bash
# 写在仓库根目录的 .env 里，和 DTK_SECRET_KEY 放一起
DTK_FORWARDED_ALLOW_IPS=172.18.0.5
```

| 取值 | api 如何对待来源地址 |
|---|---|
| 未设置（默认） | 只用对端地址。它仍然会被记录，但不会因此拒绝任何一次登录：按地址计数的失败计数器降级为一条 `auth.login_spray_suspected` 警告 |
| 你的代理地址 | 审计记录、会话列表和日志里出现真实客户端地址，按地址计的登录限制重新生效 |
| `*` | uvicorn 会相信任何人发来的 `X-Forwarded-For`，于是地址可被伪造。api 把这种情况视为“未声明”，不会据此拒绝登录 |

按地址的限制之所以是有条件的，是刻意的。在只有一个共享地址的情况下，它就是一个全局桶：陌生人二十次失败登录，
就能把所有账号锁在控制台外面十五分钟，而且可以反复来——这等于把一次拒绝服务白送给任何能访问登录页的人。按账号
的限制没有这个条件，因为五次失败只会让攻击者付出他正在猜的那个账号。

要用的地址，是 api 容器实际看到的那个。如果你的代理是同一网络上的容器，那就是它的容器地址；如果它是宿主机上
访问已发布端口的进程，那就是 Docker 网桥网关：

```bash
docker network inspect dtk_edge -f '{{ (index .IPAM.Config 0).Gateway }}'
```

对于可从互联网访问的实例，绝不要把它设成 `*`。

**流式响应。** 这个项目里没有任何 WebSocket——控制台是刻意采用 5–10 秒轮询的。但有两个流式响应，会被带缓冲
的代理破坏：

| 端点 | Content type | 说明 |
|---|---|---|
| `GET /api/v1/tasks/{task_id}/events` | `text/event-stream` | 服务端推送事件，直到任务结束，最长 300 秒。静默每满 15 秒会发一条保活注释。响应本身已经带上 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no` |
| `GET /api/v1/archive/export` | `application/x-ndjson` | 一行一条内容，按页流式输出，所以你的归档有多大不会成为导出失败的原因 |

nginx 认 `X-Accel-Buffering: no`，所以只要读超时超过 300 秒，普通的 proxy 配置就能让 SSE 正常工作：

```nginx
server {
    listen 443 ssl;
    server_name dtk.example.com;

    ssl_certificate     /etc/letsencrypt/live/dtk.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dtk.example.com/privkey.pem;

    # API 自己就会拒绝超过 1 MiB 的请求体，并返回规范的错误信封。
    # 把 nginx 的上限设在它之上，调用方才能拿到那条消息。
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # 比任务事件流 300s 的上限更长。
        proxy_read_timeout 320s;
    }
}
```

Caddy 会自己设置 `X-Forwarded-*` 头，只需要关掉缓冲：

```caddyfile
dtk.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

API 还会设置自己的安全响应头，并且不发送 `Server` 头（`--no-server-header`）。跨域默认是关闭的：
`security.cors_allow_origins` 为空，意味着仅同源。如果你要把控制台和 API 放在不同的源上，就得有意识地打开
它——见 [安全](./15-security.md)。

## 验证安装

```bash
# 每个服务都健康吗？
docker compose -p dtk -f docker/compose.yml ps

# 存活与就绪
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz

# 磁盘上是哪个迁移版本，CLI 又怎么说
docker compose -p dtk -f docker/compose.yml exec api dtk --version
docker compose -p dtk -f docker/compose.yml exec api dtk migrate --show

# 启用了 browser profile：里面真的有浏览器吗？
docker compose -p dtk -f docker/compose.yml exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://browser-rpc:9000/rpc/health').read().decode())"
```

Postgres 或 Redis 挂掉时，`/readyz` 返回 `503` 并带上分组件的明细，通常比翻日志更快。`browser-rpc` 刻意
不在其中。

仓库里还有一个端到端冒烟测试。它会用同一个 compose 文件拉起一个**独立的** compose 项目（`dtk-smoke`），
走完初始化流程、签发一个 API 密钥、跑一遍文档化的契约，然后连同数据卷一起拆掉：

```bash
./scripts/smoke.sh          # 完整运行
./scripts/smoke.sh --keep   # 保留栈以便检查
```

因为它用的是同一个 compose 文件，它也会发布同一个 `127.0.0.1:8000`。先停掉你的主栈，否则端口已被占用。

## 升级

最省事的办法是 [引导式脚本](#用脚本装和管)：再跑一次，选「升级」。它会和 GitHub 最新
Release 比版本、把 `DTK_IMAGE_TAG` 钉住、拉镜像、跑迁移、重启，并且迁移在切换容器之前
跑 —— 失败了旧容器还在。下面是它做的每一步，手工版。

**先备份。** 归档是逻辑导出，不是卷快照，而且恢复它需要同一个 `DTK_SECRET_KEY`——凭据是以加密状态导出的。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api dtk backup list --dir /var/lib/dtk/backups
```

必须显式传 `-o`：CLI 自己的默认值是相对路径 `backups/`，而容器根文件系统是只读的，所以在容器里不带参数跑
`dtk backup create` 会失败。`DTK_BACKUP_DIR` 是控制台和 worker 用的，不是 CLI 用的。除非你传
`--include-identities`，身份不会进归档，因为它们绑着代理和出口地址，而新机器并没有那些。

把归档从卷里拷出来：

```bash
docker compose -p dtk -f docker/compose.yml cp \
  "api:/var/lib/dtk/backups/<file>" ./
```

然后升级：

```bash
git pull
docker compose -p dtk -f docker/compose.yml build
docker compose -p dtk -f docker/compose.yml up -d --wait
```

`migrate` 每次启动都会跑，Alembic 是幂等的，所以表结构变更会自己应用。命名卷在重建中保留。

[运维](./10-operations.md#安全地升级)把同一次升级写成了带编号的流程：先备份，升级后再查 `/readyz` 和 migrate
日志；下面这几条对它同样适用。

有三件事需要知道：

- **`build` 只构建当前激活 profile 里的服务。** 要重建浏览器或下载器镜像，就把 profile 参数带上——并且再次
  提供 `CLOAKBROWSER_COMMIT`，因为它是构建参数，取自你的 shell 或 `docker/.env`，而不是根目录 `.env`。
- **CLI 里的迁移是单向的。** `dtk migrate` 只升不降，没有 downgrade 命令。要回滚一个改过表结构的版本，
  意味着恢复它之前的备份。
- **绝不要在升级中顺手改 `DTK_SECRET_KEY`。** 所有已存的 cookie 和代理凭据都用它加密。如果它非改不可，
  就把受影响的身份退休，然后重新铸造或导入。

如果你想自己掌握升级时机、而不是让它在容器启动时发生，就手动执行迁移：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate
```

## 扩容：增加 worker

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=4
```

每个 worker 同时跑 4 个任务，所以四个 worker 就是十六个槽位。而这几乎肯定不是限制你的东西。

**吞吐的上限来自身份池，不是 worker 数量。** 每一次上游请求都要独占一个身份的租约，并通过一个
每（身份，接口）的令牌桶。超过健康身份数量的 worker 不会带来吞吐，只会排队。在扩 worker 之前先看身份池：
`pool.target_size` 每个平台默认是 8，调高它意味着要铸造更多身份，也就意味着更多代理。见
[身份与代理](./06-identities-and-proxies.md)。

多开 worker *确实*有用的场景是：让长时间的后台工作（归档复查、关注列表 tick、大的下载作业）不阻塞交互式解析，
以及在某个 worker 挂掉时不出现停顿。如果你要服务大量命中缓存的读请求，该扩的是 API——但那意味着第二个发布
端口和一个负载均衡器，compose 文件并没有替你准备这些。

Postgres 没有 CPU 上限，Redis 是整套栈里最小的东西，所以这两个通常都不是你最先撞到的天花板。

## 不用 Docker 运行

这是受支持的，也是项目本身的开发方式。你需要：

| 要求 | 版本 |
|---|---|
| Python | `>=3.12,<3.14` |
| [uv](https://docs.astral.sh/uv/) | 任意较新版本 |
| PostgreSQL | 17，且 **timescaledb** 扩展可用 |
| Redis | 8 |
| Node | 22，仅在你想构建控制台时需要 |

先把仓库拉下来，下面的命令都在仓库根目录执行：

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

如果你手上还没有数据库和 Redis，先起一套。仓库里带了一个测试夹具用的 compose 文件，把它们发布在非默认端口上，
不会和任何东西冲突：

```bash
docker compose -p dtk-test -f docker/compose.test.yml up -d --wait
# postgres 在 127.0.0.1:55432（数据库 dtk_test），redis 在 127.0.0.1:56379
```

这个夹具把 Postgres 数据放在 tmpfs 上，`down -v` 会清空它。用于开发没问题，用于任何你想保留的东西都是错的。

安装并配置：

```bash
uv sync --all-extras

cat > .env <<'EOF'
DTK_SECRET_KEY=replace-me-with-openssl-rand-base64-48
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test
DTK_REDIS_URL=redis://127.0.0.1:56379/0
DTK_BIND_HOST=127.0.0.1
DTK_BIND_PORT=8000
EOF
```

建好表结构，然后在两个终端里分别跑两个进程：

```bash
uv run alembic upgrade head          # 或者：uv run dtk migrate
uv run dtk serve --reload            # API 在 127.0.0.1:8000
uv run dtk worker                    # 任务 worker
```

不传 `--host`/`--port` 时，`dtk serve` 监听 `DTK_BIND_HOST`/`DTK_BIND_PORT`。`--reload` 不能和
`--workers` 一起用，而且这个命令会在读取环境变量之前就告诉你这一点。

控制台有两种方式。构建一次，让 API 提供它：

```bash
cd web && npm ci && npm run build      # 产物在 web/dist
```

源码检出下 API 无需任何配置就能找到 `web/dist`；目录结构不同的话用 `DTK_CONSOLE_DIR` 覆盖查找路径。或者跑
Vite 开发服务器，它会把 `/api`、`/docs`、`/redoc`、`/openapi.json`、`/healthz` 和 `/readyz` 代理到本地
运行的 API：

```bash
cd web && npm run dev                  # http://localhost:5173
```

如果你的 API 不在 `127.0.0.1:8000`，用 `DTK_API_TARGET` 覆盖代理目标。

不用 Docker 会失去什么：只读根文件系统、丢弃的 capability、内存与 PID 上限，以及那个让 Postgres 和 Redis
无法被访问到的内部网络。这些是 compose 文件的属性，不是代码的属性。裸机生产部署得用别的方式把它们补回来——见
[安全](./15-security.md)。

可选服务同样可以手动跑。`browser-rpc` 不用容器也能从仓库里跑起来，只是在没装 CloakBrowser 的情况下只有
`fake` 后端可用：

```bash
PYTHONPATH=docker DTK_BROWSER_BACKEND=fake DTK_BROWSER_BIND_PORT=19000 \
  DTK_BROWSER_PROFILE_ROOT=/tmp/dtk-browser DTK_BROWSER_GEO_PROBE_URL= \
  uv run python -m browser_rpc
```

这对练习 RPC 接口有用，但产不出可用的身份——fake 后端产出的是任何平台都不认的合成 cookie，而这正是没有任何
东西会自动退回到它的原因。

### 在裸机上做生产部署

上面那套是开发流程——它让你用测试夹具，而那个夹具把数据放在 tmpfs 上。要在一台服务器上长期跑，
数据库和 Redis 得自己装。下面这套步骤在一台干净的 Ubuntu 24.04 上实跑验证过。

**1. PostgreSQL 与 TimescaleDB**

TimescaleDB 不在 Ubuntu 默认源里，得加两个仓库。缺了它第一个迁移脚本会带着解释拒绝执行——
请求日志、身份事件和内容快照都是超表。

```bash
sudo apt-get install -y curl ca-certificates gnupg lsb-release

sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list

curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
  | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg
echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/timescaledb.list

sudo apt-get update
sudo apt-get install -y postgresql-17 timescaledb-2-postgresql-17 redis-server
```

**2. 让 TimescaleDB 真正加载**

这一步漏了的话，`CREATE EXTENSION` 会失败，而错误信息不会告诉你原因在这里：

```bash
echo "shared_preload_libraries = 'timescaledb'" \
  | sudo tee -a /etc/postgresql/17/main/postgresql.conf
sudo systemctl restart postgresql
```

**3. 建库、建用户、启用扩展**

```bash
PGPASS=$(openssl rand -hex 24)
sudo -u postgres psql -c "CREATE USER dtk WITH PASSWORD '${PGPASS}';"
sudo -u postgres createdb -O dtk dtk
sudo -u postgres psql -d dtk -c 'CREATE EXTENSION IF NOT EXISTS timescaledb;'

# 确认扩展真的启用了，再往下走
sudo -u postgres psql -d dtk -tAc \
  "SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';"
```

**4. 代码与 Python 依赖**

用一个专用的非 root 账号跑它——这是裸机部署要自己补回来的第一件事，容器本来替你做了。

```bash
sudo useradd --system --create-home --home-dir /opt/dtk --shell /usr/sbin/nologin dtk
sudo -u dtk git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git /opt/dtk/app
cd /opt/dtk/app

curl -LsSf https://astral.sh/uv/install.sh | sudo -u dtk sh
sudo -u dtk /opt/dtk/.local/bin/uv sync --frozen --no-dev
```

`--frozen` 是照 `uv.lock` 装，不重新解析依赖；`--no-dev` 跳过测试和 lint 工具链。

**5. 构建控制台**

API 自己提供控制台，所以这一步不做的话，你会得到一个能用的接口和一个 404 的首页。

```bash
sudo apt-get install -y nodejs npm     # 需要 Node 22
cd /opt/dtk/app/web && sudo -u dtk npm ci && sudo -u dtk npm run build
```

从源码检出运行时 API 无需配置就能找到 `web/dist`。目录结构不同就用 `DTK_CONSOLE_DIR` 指过去。

**6. 配置**

```bash
sudo -u dtk tee /opt/dtk/app/.env >/dev/null <<EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
DTK_DATABASE_URL=postgresql+asyncpg://dtk:${PGPASS}@127.0.0.1:5432/dtk
DTK_REDIS_URL=redis://127.0.0.1:6379/0
DTK_BIND_HOST=127.0.0.1
DTK_BIND_PORT=8000
DTK_BACKUP_DIR=/opt/dtk/backups
EOF
sudo chmod 600 /opt/dtk/app/.env
sudo -u dtk mkdir -p /opt/dtk/backups
```

Redis 默认没有口令。它只监听回环时可以接受，但既然连它的凭据你已经在写了，不如一并设上：
在 `/etc/redis/redis.conf` 里加 `requirepass`，然后把 `DTK_REDIS_URL` 改成
`redis://:<口令>@127.0.0.1:6379/0`。

**7. 迁移，建第一个管理员**

```bash
cd /opt/dtk/app
sudo -u dtk /opt/dtk/.local/bin/uv run dtk migrate

# 口令走 stdin 而不是命令行参数：argv 对同机任何进程可见
printf '%s' 'your-password-here' \
  | sudo -u dtk /opt/dtk/.local/bin/uv run dtk user create admin --role admin --stdin
```

**8. 两个 systemd 服务**

`api` 和 `worker` 是两个进程，各自需要一个单元文件。

```ini
# /etc/systemd/system/dtk-api.service
[Unit]
Description=DTK API
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
User=dtk
WorkingDirectory=/opt/dtk/app
ExecStart=/opt/dtk/.local/bin/uv run dtk serve
Restart=always
RestartSec=5
# 容器本来提供的那部分收敛，在这里手工补回来
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/dtk/backups

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/dtk-worker.service
[Unit]
Description=DTK worker
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
User=dtk
WorkingDirectory=/opt/dtk/app
ExecStart=/opt/dtk/.local/bin/uv run dtk worker
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dtk-api dtk-worker
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/healthz    # 期望 200
```

**你放弃了什么，以及怎么补**

容器提供的是只读根文件系统、丢弃的 capability、内存与 PID 上限，以及一个让 Postgres 和 Redis
根本无法从外部访问到的内部网络。这些是 compose 文件的属性，不是代码的属性。上面的单元文件用
`ProtectSystem` / `NoNewPrivileges` 补回了一部分；剩下的靠 Postgres 和 Redis 只监听 `127.0.0.1`
（默认如此，别改），以及别把 `DTK_BIND_HOST` 改成 `0.0.0.0`。完整清单见[安全](./15-security.md)。

**两个可选组件**

`browser-rpc`（自动铸造身份）和 `downloader`（媒体落盘）在裸机上是**两个额外的进程**，各有各的依赖：
前者要一份独立的 Python 环境加 CloakBrowser 和 Chromium，后者要 Go 工具链编一个二进制。两个都可以不装——
不装浏览器就靠手工导入 Cookie，不装下载器则 `POST /api/v1/downloads` 返回 `501` 并说明原因。

只想要其中一个而不想手工装的话，混着来是完全可以的：应用跑在裸机上，这两个 sidecar 用
`docker compose --profile browser --profile downloader` 起，然后把 `DTK_BROWSER_RPC_URL` 和
`DTK_DOWNLOADER_URL` 指过去。它们之间只通过 HTTP 说话，不在乎对方跑在哪。


## 单独运行 worker

worker 和 API 是同一个镜像，只是第一个参数不同；它和 `uv run dtk worker` 也是同一份代码。它只需要三样东西：
`DTK_SECRET_KEY`、`DTK_DATABASE_URL` 和 `DTK_REDIS_URL`。它不需要入站端口，也不需要控制台构建产物。

在 compose 栈内部，事故期间手动跑一个：

```bash
docker compose -p dtk -f docker/compose.yml run --rm worker
```

要放到第二台机器上，限制不在 worker，而在于 `postgres` 和 `redis` 待在 `internal: true` 的网络上，恰恰是
为了让宿主机之外的东西够不着它们。把 worker 挪出去意味着要把这两者暴露出来，配上 TLS 或你自己的私有网络，
而 compose 文件不会替你做这件事。动手之前，请再读一遍上一节：多开 worker 很少有用，因为天花板是身份池。

如果镜像里找不到 worker 模块，它会以 `no worker entry point in this image` 退出，并提示你把
`DTK_WORKER_COMMAND` 设成你这套部署使用的命令行。那个后门是给挪动过模块的 fork 用的；标准构建不需要它。

## 推倒重来

```bash
# 停掉一切，保留数据
docker compose -p dtk -f docker/compose.yml down --remove-orphans

# 停掉一切并删除数据卷：数据库、队列、备份、媒体
docker compose -p dtk -f docker/compose.yml down -v --remove-orphans
```

`down -v` 不可逆，而且会把备份卷一起带走。先把想保留的归档从卷里拷出来（见“升级”）。如果你启动过可选服务，
记得带上 profile 参数，否则它们的容器会作为孤儿留下来。

仓库的 `Makefile` 封装了常用的几条——`make up`、`make down`、`make logs`、`make clean`——并且始终带着 `dtk`
项目名，这样不会有零散的东西被留下。

## 接下来看什么

- [配置参考](./03-configuration.md) —— 每一项运行时设置、它的代价，以及什么时候该改它
- [核心概念](./04-concepts.md) —— 身份、调度器、签名，以及为什么身份池是天花板
- [身份与代理](./06-identities-and-proxies.md) —— 无论开不开浏览器容器，怎么把身份池填满
- [用户与 API 密钥](./09-users-and-api-keys.md) —— 有了第一个管理员之后
- [运维](./10-operations.md) —— 备份、数据保留、日志、告警和容量
- [命令行参考](./13-cli.md) —— 本页用到的每一条 `dtk` 命令，以及它们的完整选项
- [安全](./15-security.md) —— compose 文件替你挡住了什么，又没挡住什么
- [故障排查](./14-troubleshooting.md) —— 栈已经起来了，但还是有东西不肯工作时
