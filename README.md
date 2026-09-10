<div align="center">
<a href="https://douyin.wtf/" alt="logo"><img src="./logo/logo.svg" width="120" alt="Douyin_TikTok_Download_API"/></a>
</div>
<h1 align="center">Douyin_TikTok_Download_API</h1>

<div align="center">

[English](./README.en.md) | [简体中文](./README.md)

🚀 自部署的[抖音](https://www.douyin.com) / [TikTok](https://www.tiktok.com)数据接口服务。一条 `docker compose up`，自动维护身份池，对外提供 REST API、MCP 与 Web 控制台。

[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)
[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)
[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)
<br>
[![CI](https://img.shields.io/github/actions/workflow/status/Evil0ctal/Douyin_TikTok_Download_API/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/actions/workflows/ci.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/evil0ctal/douyin_tiktok_download_api?style=flat-square&logo=docker&color=2496ed)](https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api)
[![Docker Image Size](https://img.shields.io/docker/image-size/evil0ctal/douyin_tiktok_download_api/latest?style=flat-square&logo=docker&color=2496ed)](https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api/tags)

</div>

## 💖 赞助商

这些赞助商已付费放置在这里，**Douyin_TikTok_Download_API** 项目将永远免费且开源。如果您希望成为该项目的赞助商，请查看我的 [GitHub 赞助商页面](https://github.com/sponsors/evil0ctal)。

<div align="center">
    <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_logo" target="_blank" rel="sponsored noopener">
        <img src="https://tikhub.io/logo.jpeg" width="100" alt="TikHub.io - 全球社交媒体数据与 API 市场"/>
    </a>
    <h2>
        <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_name" target="_blank" rel="sponsored noopener"><b>TikHub.io</b></a>
    </h2>
    <p>你的一站式社交媒体数据与 API 市场</p>
    <p>
        为抖音、小红书、TikTok、Instagram、YouTube、Twitter 等平台提供专业数据方案。<br>
        实时数据 · 灵活接口 · 无缝集成 · 有竞争力的价格与折扣
    </p>
    <p>
        在 TikHub.io 市场买卖定制 API、服务与社交媒体解决方案，<br>
        加入一个由开发者、企业与内容创作者组成的活跃生态。
    </p>
    <p><em>多家全球领先的红人营销与社交媒体情报平台正在使用</em></p>
    <p>
        <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_cta" target="_blank" rel="sponsored noopener"><b>→ 前往 TikHub.io</b></a>
        &nbsp;·&nbsp;
        <a href="https://api.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_docs" target="_blank" rel="sponsored noopener">API 文档</a>
    </p>
</div>

## 🎬 长这样

<div align="center">
    <img src="./screenshots/console-zh.gif" width="900" alt="DTK 控制台：总览、身份池、调度器、调试台、资料库、下载、接口文档与 MCP"/>
</div>

一次真实的调用：粘链接、发请求、拿到归一化后的结果，途中经过的身份池、调度器和接口文档都在同一个控制台里。
界面跟着浏览器语言走，中英文都是手写的，不是机翻。[English UI](./screenshots/console-en.gif)

## 🚀 v4 与 v5

v5 是一次重写，从空分支起步，没有继承 v4 的任何代码。

v4 最大的问题从来不是功能少，而是**接口会悄悄死掉，而你不知道**。Cookie 过期、签名算法变更、
某个接口被风控，通常都要等到有人来提 issue 才发现。v5 把"看得见"和"能自愈"排在功能前面。

| | v4 | v5 |
|---|---|---|
| 身份从哪来 | 从浏览器里抠 Cookie，粘进 `config.yaml` | 无头浏览器自动铸造游客身份，可用数不够时自己补 |
| 请求怎么发 | 有请求就发出去 | 健康度分层、量化 LRU 轮换、每身份独占锁、每（身份，接口）令牌桶、接口级熔断 |
| 出了问题 | 等别人报错 | 每次请求一条结构化记录，身份和接口都有实时健康度，控制台上看得见 |
| 调用方式 | 同步，发出去等着 | 默认异步（`202` + `task_id`），加 `?wait=` 就退回同步 |
| 数据留存 | 解析完即弃 | PostgreSQL + Redis；解析过的内容自动归档，平台删了这里还在 |
| 访问控制 | 没有，谁都能调 | API Key + 作用域 + 角色，控制台里管 |
| 界面 | PyWebIO 单页 | React 控制台：身份池、调度器、资料库、下载、日志、诊断 |
| 接入方式 | REST | REST + MCP + CLI，共用同一个 service 层 |
| 签名 | X-Bogus、A_Bogus | a_bogus、X-Bogus、X-Gnarly、X-Dynosaur，纯 Python 实现，另有浏览器兜底 |
| 部署 | `pip install -r requirements.txt` + `python start.py` | `docker compose up`，三个镜像 |
| 平台 | 抖音、TikTok、哔哩哔哩 | 抖音、TikTok |

哔哩哔哩是唯一退步的一项：v5 暂时没有。它和抖音 / TikTok 不共用签名和身份体系，
重写时先放下了，之后再说。

### 还在用 v4？

v4 的代码保留在 [`v4` 分支](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4)，
镜像也还在，按版本号拉就行：

```bash
docker pull evil0ctal/douyin_tiktok_download_api:V4.1.2
```

`main` 现在是 v5，`latest` 跟着 `main` 走。想留在 v4 上就固定版本号 tag，别用 `latest`。

### 一起写点什么

有几个开源项目的交流群，想一起做这个项目、或者只是想聊聊技术的，
都可以加微信 **`Evil0ctal`**，备注 **github 交流**，我拉你进群。

群里可以互相交流学习，**不允许发广告以及违法的东西**，纯粹交朋友和技术交流。


## 📦 它能取什么

| 能力 | 抖音 | TikTok |
|---|:---:|:---:|
| 单条作品详情（视频 / 图集） | ✅ | ✅ |
| 作者资料 | ✅ | ✅ |
| 作者作品列表 | ✅ | ✅ |
| 作者喜欢列表 | ✅ | ✅ |
| 合集 / 播放列表 | ✅ | ✅ |
| 评论 | ✅ | ✅ |
| 评论回复 | ✅ | ✅ |
| 粉丝列表 | ❌ | ✅ |
| 关注列表 | ❌ | ✅ |

抖音的粉丝和关注列表只对已登录会话开放，游客身份拿不到，所以这两个接口干脆没注册：
注册一个永远返回空页的接口没意义。导入你自己的登录 Cookie 之后，其余接口能看到的内容也会更多。

媒体下载、内容归档、计数快照、合集、定时监控都是内置的，不需要额外服务。

## 🔗 它认得什么链接

粘贴什么都行——分享短链、作品页地址，或者 App 复制出来的一整段带文案的口令：

```
https://v.douyin.com/L4NpDJ6/
https://www.douyin.com/video/7126745726494821640
https://www.douyin.com/jingxuan?modal_id=7660875690212492466
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
https://www.tiktok.com/t/ZTR9nkkmL/
2.84 nqe:/ 骑白马的也可以是公主%%百万转场变身 https://v.douyin.com/L4FJNR3/ 复制此链接，打开Dou音搜索
```

短链会自动跟随展开，文案里的链接会被提取出来。作品 ID 还会先按平台自己的编码规则校验一遍，
一个不可能存在的 ID 在这里就被拒掉，不会浪费一次上游请求。

## ⚗️ 技术栈

| | |
|---|---|
| 服务端 | Python 3.12 · FastAPI · SQLAlchemy 2.0（async）· Alembic · Typer · structlog |
| 传输 | wreq（浏览器 TLS 指纹模拟）· httpx |
| 数据 | PostgreSQL + TimescaleDB · Redis |
| 控制台 | React 19 · TypeScript · Vite · TanStack Query · wouter · i18next |
| 签名 | a_bogus / X-Bogus / X-Gnarly / X-Dynosaur，纯 Python 实现 |
| 身份铸造 | CloakBrowser 无头浏览器，单独一个容器，通过 HTTP 调用 |
| 下载器 | Go 1.23，只用标准库，静态编译进 scratch 镜像 |
| 鉴权 | argon2id 口令哈希 · API Key + 作用域 |
| 对外协议 | REST（OpenAPI）· MCP（streamable-http）· CLI |
| 工程 | uv · ruff · mypy · pytest · Docker Compose |

除了 Postgres 和 Redis，不需要任何其它基础设施——没有 Kafka、没有 Elasticsearch、
没有对象存储、没有 k8s。

CloakBrowser 的版本 pin 在一个指定的 commit 上。这是一项安全控制，见
[docker/Dockerfile.browser](./docker/Dockerfile.browser)。

## 🗂 项目结构

```
Douyin_TikTok_Download_API/
├── src/dtk/                服务端，代码全在这
│   ├── api/                FastAPI 路由、鉴权、OpenAPI 本地化
│   ├── platforms/          抖音 / TikTok 适配器：接口定义、参数、解析
│   ├── signing/            a_bogus / X-Bogus / X-Gnarly / X-Dynosaur
│   ├── transport/          出站请求、响应分类（正常 / 业务错误 / 风控 / 网络）
│   ├── identity/           身份铸造与健康度
│   ├── scheduler/          身份选取、令牌桶、熔断器
│   ├── services/           业务层，REST、MCP、CLI 共用
│   ├── worker/             异步任务、回调、定时收集
│   ├── db/                 SQLAlchemy 模型与 Alembic 迁移
│   ├── ops/                诊断、备份、健康检查、告警通知
│   ├── media/              下载器客户端
│   ├── models/             跨平台统一的内容模型
│   ├── urls/               链接识别、短链展开、ID 校验
│   ├── mcp/                MCP server
│   ├── cli/                dtk 命令行
│   ├── i18n/               服务端中英文案
│   └── core/               配置、日志、错误类型
├── web/                    React 控制台，构建产物打进应用镜像
│   └── src/
│       ├── pages/          每个控制台页面一个文件
│       ├── components/     设计系统与共用组件
│       └── locales/        控制台中英文案
├── docker/                 三个 Dockerfile、compose 与两个 sidecar
│   ├── browser_rpc/        Python，包装 CloakBrowser
│   ├── downloader/         Go，媒体下载 sidecar
│   └── compose.yml
├── documents/              用户文档，中英各 17 篇
├── tests/                  unit / integration / contract / replay
├── scripts/                smoke.sh
├── .github/workflows/      CI 与 Docker 镜像发布
├── alembic.ini
├── pyproject.toml
└── Makefile
```

## ⚡️ 快速开始

装法有两种，下面走的是推荐的那种。手动装依赖的路子见 [不用 Docker](#不用-docker)。

| 装法 | 适合谁 | 详细步骤 |
|---|---|---|
| **Docker Compose**（推荐） | 绝大多数人，生产也包括在内 | [安装与部署 · 首次安装](./documents/zh/02-installation.md#首次安装) |
| 手动装依赖 | 不能用 Docker，或者你要改代码 | [开发方式](./documents/zh/02-installation.md#不用-docker-运行) · [裸机生产部署](./documents/zh/02-installation.md#在裸机上做生产部署) |

> **在中国大陆的机器上装？** 先换源再动手，不然多半卡在拉镜像那一步：
> [中国大陆的网络准备](./documents/zh/02-installation.md#中国大陆的网络准备)。

需要 Docker 和 Docker Compose。仓库里不带任何默认密码或密钥，先生成 `.env`：

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

```bash
docker compose -p dtk -f docker/compose.yml up -d
docker compose -p dtk -f docker/compose.yml logs api   # 打印首次初始化令牌
```

打开 <http://127.0.0.1:8000>，用日志里的令牌创建第一个管理员账号。

### 镜像从哪来

默认是本地构建：`up` 第一次会用仓库里的 Dockerfile 把应用镜像编出来，几分钟。
如果你不想在自己机器上编译，把 compose 指向已发布的镜像即可：

```bash
export DTK_IMAGE=evil0ctal/douyin_tiktok_download_api
export DTK_IMAGE_TAG=latest
docker compose -p dtk -f docker/compose.yml pull
docker compose -p dtk -f docker/compose.yml up -d
```

`pull` 这一步不能省：compose 里这些服务同时写了 `image` 和 `build`，本机没有这个镜像时
它会自己去编，而不是去拉。

镜像同时提供 `linux/amd64` 与 `linux/arm64`，所以 Apple Silicon 和树莓派上都是原生运行。
浏览器容器不发布——它会从一个指定的 commit 安装 CloakBrowser，那个 pin 是安全控制，
应当由你自己决定，所以它一直是本地构建。

### 不用 Docker

也支持，而且这就是这个项目本身的开发方式。你要自己准备 PostgreSQL 17（**必须带 TimescaleDB 扩展**，
普通 `postgres:17` 不行）、Redis 8、Python 3.12 和 uv；要构建控制台还需要 Node 22。两份步骤：

- [不用 Docker 运行](./documents/zh/02-installation.md#不用-docker-运行) —— 开发用，数据库和 Redis 仍然用容器起
- [在裸机上做生产部署](./documents/zh/02-installation.md#在裸机上做生产部署) —— 全手工，含 systemd unit，在干净的 Ubuntu 24.04 上实跑验证过

容器本来替你做的那些事——非 root 用户、内存和 CPU 上限、只读根文件系统、进程守护——裸机上都得自己补回来，
那一节把它们逐条列了出来。

### 还想知道什么

| 想做的事 | 去哪看 |
|---|---|
| 改容器规格、算机器要多大 | [三档配置](./documents/zh/02-installation.md#三档配置) · [怎么改这些上限](./documents/zh/02-installation.md#怎么改这些上限) |
| 开浏览器容器、下载器 sidecar | [两个可选 profile](./documents/zh/02-installation.md#两个可选-profile) |
| 挂 TLS、放到反向代理后面 | [放在反向代理后面](./documents/zh/02-installation.md#放在反向代理后面) |
| 环境变量到底怎么读的 | [环境变量](./documents/zh/02-installation.md#环境变量) |
| 装完确认它真的好了 | [验证安装](./documents/zh/02-installation.md#验证安装) |
| compose 文件本身逐行解释 | [docker/README.md](./docker/README.md) |

## 🖥 用它做什么

| 入口 | 地址 | 说明 |
|---|---|---|
| Web 控制台 | `/` | 身份池、调度器、资料库、下载、日志、诊断 |
| 接口文档 | `/docs` | 控制台内的 Swagger UI，中英双语 |
| 裸接口文档 | `/swagger`、`/redoc` | 无需登录 |
| REST API | `/api/v1/...` | 93 个操作 |
| MCP | `/mcp` | 与 REST 共用同一个 service 层，配置方法见控制台 `/mcp-guide` |
| CLI | `dtk --help` | 同上 |

主要能力：

- **解析**：链接、分享文案、短链、作品 ID 都吃
- **归档**：解析过的内容自动入库，平台删了这里还在
- **下载**：媒体存到自己磁盘，支持按作者批量、跳过已下载、去重
- **关注列表**：定期重新收集某个作者或作品
- **iOS 快捷指令**：`/api/v1/ios/shortcut`

## 🔄 更新

控制台会替你留意：`system.check_updates` 默认开着，登录后如果有新版本会弹一次，
每 24 小时最多一次。那个请求是**你的浏览器**发给 GitHub 的，服务器不会往外发任何东西
——所以它不会告诉任何人你这台实例存在。气人的话在设置里关掉。

更新本身就是拉镜像加重启：

```bash
cd /opt/dtk && git pull

# 用已发布镜像的（推荐）：把 .env 里的 DTK_IMAGE_TAG 改成新版本
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

自己构建的话把 `pull` 换成 `build`。`migrate` 每次启动都会跑，Alembic 是幂等的，
所以单独跑那一步只是想在切换容器之前先把库升上去。

**你的数据不会动。** 命名卷（`postgres-data`、`redis-data`、`media-data`）不随容器
重建而消失，身份池、归档、设置和 API Key 都在原地。

浏览器镜像只有在 `docker/Dockerfile.browser` 或 CloakBrowser 的 pin 变了才需要重建，
一般的版本更新不用管它。

想回退就把 `DTK_IMAGE_TAG` 改回上一个 `sha-` 或版本号再 `up -d`。
迁移不提供自动降级——升级前备份数据库，这是唯一稳妥的回退路径。


## 📖 文档

完整文档在 [`documents/`](./documents/README.zh-CN.md)，中英双语，共 17 篇。

**新手从这里开始：**
[快速开始](./documents/zh/01-quickstart.md) ·
[核心概念](./documents/zh/04-concepts.md) ·
[控制台总览](./documents/zh/05-console-overview.md)

**部署与运维：**
[安装与部署](./documents/zh/02-installation.md) ·
[配置参考](./documents/zh/03-configuration.md) ·
[运维](./documents/zh/10-operations.md) ·
[故障排查](./documents/zh/14-troubleshooting.md) ·
[安全](./documents/zh/15-security.md)

**开发对接：**
[REST API 指南](./documents/zh/11-api.md) ·
[MCP 与 AI 客户端](./documents/zh/12-mcp.md) ·
[命令行参考](./documents/zh/13-cli.md) ·
[参与开发](./documents/zh/16-contributing.md)

接口参考手册不在文档里，而是由代码直接生成的：你自己的实例在 `/docs`（控制台内）
以及 `/swagger`、`/redoc`、`/openapi.json`（无需登录）提供，中英双语。

English documentation: [`documents/README.md`](./documents/README.md)

## 📮 联系方式

| | |
|---|---|
| 问题反馈 | [GitHub Issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues) —— 公开、有记录，遇到过同样问题的人也能回答你 |
| 邮箱 | `Evil0ctal1985@gmail.com` —— 直达作者本人，适合不方便公开的内容 |
| 作者 | [@Evil0ctal](https://github.com/Evil0ctal) |

提问之前请先看[故障排查](./documents/zh/14-troubleshooting.md)，并附上自检页面或
`dtk diagnose` 的输出。它能回答维护者原本要反过来问你的大部分问题。

## ⭐️ Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

> 起始于 2021/11/06 · GitHub [@Evil0ctal](https://github.com/Evil0ctal)

## 📄 许可协议

[Apache License 2.0](./LICENSE)。

你可以使用、修改、分发本项目，**包括商业用途，也包括放进闭源产品**。这项授权不可撤销。
作为交换，协议要求你：

- 分发副本时保留版权声明和协议全文
- 在你改动过的文件里说明改了什么
- 接受它不提供任何担保

### 作者的一个请求

这个项目是免费送出去的，它能一直免费是因为有赞助商，而不是因为向用户收费。
**如果你靠它赚到了钱，请考虑也赞助一下，而不是只拿。**

这是一个请求，不是协议条款——Apache 2.0 允许商业用途，上面这段话不会把这项权利收回去。

## ☕️ 赞赏作者

上面的赞助商付的是**项目**的钱；这一节是给**维护它的人**的，完全自愿。

| 网络 | 地址 |
|---|---|
| Solana | `HvtkxmDERbNXfCoojpdFAYN5mSWowjpXgedsG9eF7y9z` |
| Tron (TRC20) | `TQwSM2vjcnrdRU7gY7KNp2tCgMnK33azkT` |
| Ethereum (ERC20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| BNB Smart Chain (BEP20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| Bitcoin | `bc1q785j55cxlnjqe8lkwy8cq57t8t9vn3ak9tlsfy` |

这些网络都支持常见的主流代币。**TRC20 或 Solana 上的 USDT** 最方便接收，手续费也最低。

> **只能按地址所属的网络转账。** 转错链的资产任何人都无法找回。
> 以太坊和 BNB Smart Chain 共用同一个地址是正常的：两者都是 EVM 链，由同一把私钥控制。

也可以走 [GitHub Sponsors](https://github.com/sponsors/evil0ctal)。

### 你需要自己负责的部分

本项目从有自己服务条款的平台抓取数据，并且运行在你自己控制的机器上。
请遵守平台条款和所在地法律，尊重你所收集内容背后的人，
不要拿它去骚扰任何人，也不要拿它去二次分发不属于你的作品。这些没有别人能替你把关。
