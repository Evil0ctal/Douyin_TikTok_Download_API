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
[![爱发电](https://img.shields.io/badge/爱发电-evil0ctal-blue.svg?style=flat-square&color=ea4aaa&logo=github-sponsors)](https://afdian.net/@evil0ctal)
[![Kofi](https://img.shields.io/badge/Kofi-evil0ctal-orange.svg?style=flat-square&logo=kofi)](https://ko-fi.com/evil0ctal)
[![Patreon](https://img.shields.io/badge/Patreon-evil0ctal-red.svg?style=flat-square&logo=patreon)](https://www.patreon.com/evil0ctal)

</div>

## 赞助商

这些赞助商已付费放置在这里，**Douyin_TikTok_Download_API** 项目将永远免费且开源。如果您希望成为该项目的赞助商，请查看我的 [GitHub 赞助商页面](https://github.com/sponsors/evil0ctal)。

<div align="center">
    <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme" target="_blank" rel="sponsored noopener">
        <img src="https://tikhub.io/logo.jpeg" width="100" alt="TikHub.io - Global Social Data & API Marketplace">
    </a>
    <div>
        <h2><b>TikHub.io</b></h2>
        <p>Your Ultimate Social Media Data & API Marketplace</p>
        <p>
            Professional data solutions for Douyin, Xiaohongshu, TikTok, Instagram, YouTube, 
            Twitter, and more.<br>
            Real-time Data | Flexible APIs | Seamless Integration | Competitive Pricing with Discounts
        </p>
        <p>
            <b>Discover TikHub.io Marketplace</b><br>
            Buy and sell custom APIs, services, and social media solutions.<br>
            Join a thriving ecosystem of developers, businesses, and content creators.
        </p>
        <p><em>Trusted by leading global influencer marketing and social media intelligence platforms</em></p>
    </div>
</div>


## v5 是什么

v5 是一次重写，从空分支起步，不继承 V4 的代码。

V4 最大的问题不是功能少，是**接口会悄悄死掉而没人知道**：cookie 过期了、签名算法变了、
某个接口被风控了，你只有在别人报错时才发现。v5 把"可观测 + 可自愈"排在功能前面。

它做三件事：

1. **自己维护身份。** 用无头浏览器铸造游客身份（cookie + 指纹 + 代理），
   健康度低于阈值自动补充。你不用再从浏览器里抠 cookie 粘进配置文件。
2. **把请求摊开。** 调度器按健康度分层、量化 LRU 轮换、每身份独占锁、
   每（身份，接口）令牌桶、接口级熔断器。单个身份不会承担大量请求。
3. **把发生的事记下来。** 每次请求一条结构化记录，每个身份和接口都有实时健康度，
   风控信号触发冷却与熔断，控制台上看得见。

### 明确不做的事

- ❌ AI 内容分析 / 生成 / 向量检索
- ❌ **任何商业化功能**：计费、套餐、配额售卖、订阅、多租户。
  文档里的 `rate_limit`、`quota` 一律指防滥用限额，与收费无关
- ❌ Kafka、Elasticsearch、MinIO、k8s。Postgres + Redis 能解决的不引入新组件
- ❌ 服务器中转视频流量

## 快速开始

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

更多部署细节（浏览器容器、下载器 sidecar、反向代理、备份）见 [docker/README.md](./docker/README.md)。

## 用它做什么

| 入口 | 地址 | 说明 |
|---|---|---|
| Web 控制台 | `/` | 身份池、调度器、资料库、下载、日志、诊断 |
| 接口文档 | `/docs` | 控制台内的 Swagger UI，中英双语 |
| 裸接口文档 | `/swagger`、`/redoc` | 无需登录 |
| REST API | `/api/v1/...` | 88 个操作 |
| MCP | `/mcp` | 与 REST 共用同一个 service 层，配置方法见控制台 `/mcp-guide` |
| CLI | `dtk --help` | 同上 |

主要能力：

- **解析**：链接、分享文案、短链、作品 ID 都吃
- **归档**：解析过的内容自动入库，平台删了这里还在
- **下载**：媒体存到自己磁盘，支持按作者批量、跳过已下载、去重
- **关注列表**：定期重新收集某个作者或作品
- **iOS 快捷指令**：`/api/v1/ios/shortcut`

## 文档

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
## 许可协议

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

## 赞赏作者

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

也可以走 [GitHub Sponsors](https://github.com/sponsors/evil0ctal) 或 [爱发电](https://afdian.net/@evil0ctal)。

### 你需要自己负责的部分

本项目从有自己服务条款的平台抓取数据，并且运行在你自己控制的机器上。
请遵守平台条款和所在地法律，尊重你所收集内容背后的人，
不要拿它去骚扰任何人，也不要拿它去二次分发不属于你的作品。这些没有别人能替你把关。
