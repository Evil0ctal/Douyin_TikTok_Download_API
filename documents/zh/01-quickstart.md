# 快速开始

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** ——
> 自部署的抖音 / TikTok 数据接口服务：REST、MCP 和 Web 控制台，身份池自维护。
> [文档首页](../README.zh-CN.md) · [English](../en/01-quickstart.md)

读完这一页，你会在一台主机上跑起整套服务、创建好管理员账号，并成功解析第一条链接——
先在 Web 控制台里解析一次，再用 `curl` 加 API Key 解析一次。大约需要十分钟，
外加第一次构建镜像的时间。

下面所有命令都在仓库根目录执行，并且每条 compose 命令都同时带上项目名 `-p dtk`
和 compose 文件 `-f docker/compose.yml`。`docker/compose.yml` 里已经写了
`name: dtk`，所以 `-p dtk` 只是把目标写明，并不会改变它——但仍然请保留它。
真正不能少的是 `-f`：仓库根目录下没有 compose 文件，少了它命令根本找不到任何配置。

## 开始之前

| 要求 | 说明 |
|---|---|
| Docker Engine + Compose v2 | 用 `docker compose version` 确认。compose 文件用了 `env_file` 的长语法（`path:` / `required:`），需要 Compose v2.24 或更高版本。 |
| `git` | 用来获取源码。本项目没有发布镜像仓库，应用镜像会在第一次 `up` 时在本地构建。 |
| `openssl` | 第 2 步用它生成密钥。任何 CSPRNG 都可以，下面的命令用的是 `openssl`。 |
| 内存 | 核心服务实测 2 GB 就够用；`docker/compose.yml` 里各容器的上限加起来是 3.5 GiB，所以 4 GB 是更稳妥的数字。如果同时跑浏览器容器，建议 8 GB。 |
| 磁盘 | 核心服务的镜像约 4.5 GB，加上浏览器容器约 6.3 GB，这还不含数据卷和构建缓存。留 15 GB 空闲比较从容。 |
| 出网 | 容器需要能访问 Docker Hub 拉取 Postgres 和 Redis，以及（直连或经代理）访问目标平台。 |

镜像体积，取自一套可用环境上的 `docker images`：

| 镜像 | 来源 | 占用磁盘 |
|---|---|---|
| `timescale/timescaledb-ha:pg17` | 拉取 | 约 3.9 GB |
| `dtk-app:dev` —— api、worker、migrate | 本地构建 | 约 420 MB |
| `redis:8-alpine` | 拉取 | 约 160 MB |
| `dtk-browser-rpc:dev` —— `--profile browser` | 本地构建 | 约 1.7 GB |
| `dtk-downloader:dev` —— `--profile downloader` | 本地构建 | 约 9 MB |

内存占用：左列是实测的空载值，右列是 `docker/compose.yml` 给每个服务设的上限。
设上限是为了让某一个组件过载时不至于拖垮其余部分，而不是给服务规划的预算：

| 服务 | 空载 | compose 上限 |
|---|---|---|
| `postgres` | 112 MiB | `mem_limit: 2g` |
| `api` | 104 MiB | `mem_limit: 512m` |
| `worker` | 71 MiB | `mem_limit: 512m` |
| `redis` | 8 MiB | `mem_limit: 512m` |
| `browser-rpc` | 预热后 2.57 GiB、CPU 629% | `mem_limit: 4g`、`cpus: 4.0` |
| `downloader` | 每个文件一个 32 KiB 缓冲区的流式传输 | `mem_limit: 512m` |

8 GB 这个数字完全是 `browser-rpc` 带来的。它的浏览器 profile 放在 tmpfs 上
（`/tmp` 3g、`/profiles` 1g），而 tmpfs 吃的是内存，不是磁盘。

## 第 1 步 —— 获取源码

v5 是一次重写，与 V4 不共用任何代码。克隆仓库即可，默认分支就是 v5：

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

继续之前先确认拿到的是对的代码树——如果这个文件不存在，说明你在 V4 上，
下面的内容一条都不适用：

```bash
ls docker/compose.yml
```

## 第 2 步 —— 写 `.env`

本仓库不附带任何默认密码或密钥，容器在没有密钥的情况下会拒绝启动。
`DTK_SECRET_KEY` 是加密所有已存 cookie 和代理凭据的主密钥；在每一份安装里都
相同的密钥根本算不上加密，所以这里没有可以“兜底”的默认值。

文件要写在**仓库根目录**，不是 `docker/` 目录下：

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

| 变量 | 含义 |
|---|---|
| `DTK_SECRET_KEY` | 凭据加密的主密钥。至少 32 个字符，否则每种角色都会在连接数据库之前以 `78`（`EX_CONFIG`）退出。 |
| `POSTGRES_PASSWORD` | `dtk` 数据库角色的密码，由 postgres 容器读取。 |
| `REDIS_PASSWORD` | Redis 的 `requirepass`。为空时 redis 容器直接拒绝启动。 |
| `DTK_DATABASE_URL` | 应用连接 Postgres 的地址。它重复了 `POSTGRES_PASSWORD`，所以上面的脚本直接拼好，免得你手抄出错。 |
| `DTK_REDIS_URL` | Redis 的同类配置。 |

两个能省不少时间的细节：

- 两个密码生成的是 **hex 而不是 base64**，因为它们最终会出现在 URL 里，
  而 `+` 和 `/` 在那里需要转义。
- `.env` 已被 git 忽略，也写进了 `.dockerignore`，所以它只会作为 env file
  传给容器，绝不会被打进镜像。

`.env` 只是引导层。其余配置——缓存 TTL、身份池大小、数据保留期、限流——都在
首次初始化时从环境变量里播种一次，之后存在数据库中，通过控制台修改。
参见[配置参考](./03-configuration.md)。

> 事后更换 `DTK_SECRET_KEY`，会让已经存储的 cookie 和代理凭据无法解密。
> 这不是不可恢复——把受影响的身份退休，重新铸造或导入即可——但也不是没有代价。
> 生成一次就够了，并且要像对待数据库一样认真备份它。

## 第 3 步 —— 决定要不要跑浏览器容器

这个决定直接决定你的第一次请求能不能成功，所以现在就想清楚，别等到第 9 步才发现。

服务维护着一个**身份**池：cookie、浏览器指纹和可选的代理捆绑在一起，由调度器轮换。
身份只有两个来源，其中一个属于可选的基础设施。

**跑浏览器容器**（`--profile browser`，并把 `DTK_BROWSER_RPC_URL` 指向它）：
无头浏览器会自动铸造访客身份。worker 每 60 秒检查一次每个平台，**每次只铸造一个
身份**，失败时指数退避，直到身份池从低水位 `pool.min_size`（默认 3）回到
`pool.target_size`（默认 8）。因此补池要花上几分钟，这是有意为之：
一秒之内从同一个部署里冒出五位“新访客”，本身就比这些身份之后的任何行为都更像异常。

**不跑它**（默认状态，`DTK_BROWSER_RPC_URL` 为空）：补池任务被整个跳过，
**身份池一开始就是空的，而且会一直空着**。具体表现为：

- 控制台「身份池」页面显示无法铸造，初始化向导的铸造步骤也会直接说明，
  而不是报错。
- `POST /api/v1/admin/identities/mint` 依然会受理请求——它返回 `202` 和 `task_ids`——
  随后排队的任务以 `501 NOT_CONFIGURED` 失败，消息是“本实例未配置该功能。”，
  并带上 `error.details.reason: "browser_rpc_unconfigured"`。mint 调用不接受 `?wait=`，
  所以这个结论要在任务上（`GET /api/v1/tasks/<task_id>`）或控制台里看，
  而不是在 mint 本身的响应里。
- 所有内容请求都会失败，返回 `503 IDENTITY_POOL_EXHAUSTED` ——
  “当前无可用身份，预计 N 秒后恢复” —— 直到你至少导入一份 cookie。

不跑浏览器是受支持的部署方式，不是坏掉的部署：手动导入 cookie 的用户不需要整套
服务里最重的那个容器，而且当 `browser-rpc` 不可达时，身份池会自动退回到手动导入。
但它确实不是开箱即用。二选一：

| 路线 | 怎么做 | 代价 |
|---|---|---|
| 自动铸造 | 第 4 步加上 `--profile browser` | 约 1.7 GB 镜像、预热后 2.57 GiB 内存、最多 4 核 CPU、构建更久 |
| 手动 cookie | 跳过该 profile，第 6 步之后在「身份池」页面导入 | cookie 由你提供并负责续期；一份已登录的 cookie 等同于账号密码 |

如果选浏览器，有一个所有人都会踩的坑：`CLOAKBROWSER_COMMIT` 是在**构建时**读取的，
而且它是 compose 的*插值*变量——compose 从 `docker/.env` 或你的 shell 里读这类变量，
**不会**读仓库根目录的 `.env`。在命令行上传入才是可靠做法。
它刻意钉死到某个 commit 而不是 tag，因为 GitHub 上有多个组织发布了描述完全相同的
`cloakbrowser` 仓库，浮动 tag 无法唯一确定是哪一份软件。
如果构建时这个值为空，镜像里根本不含浏览器，并且会在 `/rpc/health` 上如实说明，
而不是悄悄铸造出平台根本不认的合成身份。

## 第 4 步 —— 启动服务

核心服务（postgres、redis、migrate、api、worker）：

```bash
docker compose -p dtk -f docker/compose.yml up -d --wait --wait-timeout 300
```

第一次运行会构建 `dtk-app`——一个 Node 阶段构建控制台，一个 `uv sync --frozen`
阶段装 Python 依赖——所以要等上几分钟。`--wait` 会等到 API 的 `/readyz` 探针通过才返回，
所以命令成功退出意味着服务真的能对外提供请求，而不只是容器存在。

连浏览器容器一起启动：

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
docker compose -p dtk -f docker/compose.yml --profile browser ps
```

那个 commit 对应 CloakBrowser 0.5.10 / Chromium 146，是本仓库端到端验证过的版本；
`.env.example` 里写它也是同样的理由——但那只是一个参考值，不是一份能直接生效的配置。
把 `.env.example` 复制成仓库根目录的 `.env` **并不会**让这个 pin 进入构建，
因为 compose 的插值不读那个文件：要么像上面那样写在命令行上，要么放进 `docker/.env`，
要么用 `COMPOSE_ENV_FILES=.env` 把根目录文件喂给 Compose。要改就有意识地改。
`browser-rpc` 的健康检查 `start_period` 是 60 秒，所以别急着用 `ps` 的结果下结论，
给它一分钟。

### API 发布在哪里

只有 `api` 会发布端口，默认绑定在回环地址 `127.0.0.1:8000`。这个地址来自 compose
的*插值*，它读的是 `docker/.env` 或你的 shell，**不是**仓库根目录的 `.env`。
所以在根目录 `.env` 里设 `DTK_BIND_HOST` 或 `DTK_BIND_PORT` 完全没有效果：
api 容器自己的取值在 `compose.yml` 里被钉死了，而发布地址又不从那个文件读。

| 你想要 | 命令 |
|---|---|
| 只监听回环（默认） | `docker compose -p dtk -f docker/compose.yml up -d` |
| 监听所有网卡 | `DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d` |
| 让根目录 `.env` 生效 | `COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d` |

对外提供服务意味着你要自己在前面加 TLS，并设置 `DTK_FORWARDED_ALLOW_IPS`，
好让按地址计数的登录失败限制针对单个调用方，而不是整个互联网。这两件事都不会
自动完成。在把端口暴露到回环之外以前，先看[安装与部署](./02-installation.md)
和[安全](./15-security.md)。

Postgres 和 Redis 挂在一个声明为 `internal: true` 的网络上——完全没有出主机的路由——
并且都不发布端口。这是刻意的，别为了“方便调试”去改它。

## 第 5 步 —— 找到初始化令牌

全新部署没有任何账号，这就留下了一个窗口：谁先到，谁就成了管理员。
靠来源地址判断关不上这个窗口：Docker 发布端口用的 userland proxy 会把对端地址
改写成网桥网关，于是“只接受私有地址”等于放行整个互联网。
真正的闸门是一次性令牌，它只存在于容器日志里。

```bash
docker compose -p dtk -f docker/compose.yml logs api
```

找这段横幅：

```text
==========================================================================
  dtk is not initialized yet.
  Open this URL to create the administrator account:

    http://127.0.0.1:8000/setup?token=Xf3k...

  The token expires in 24 hours.
  To issue a new one: docker compose restart api
==========================================================================
```

横幅最后那行是简写，并不完整：`docker compose restart api` 既没有 `-p dtk`，
也没有 `-f docker/compose.yml`，而仓库根目录下没有 compose 文件，
所以照抄执行会以 `no configuration file provided` 失败。真正能用的是
`docker compose -p dtk -f docker/compose.yml restart api`。

或者直接把 URL 抠出来：

```bash
docker compose -p dtk -f docker/compose.yml logs api \
  | grep -oE 'http://[^ ]*/setup\?token=[A-Za-z0-9_-]+' | tail -1
```

关于这个令牌的事实：

| 属性 | 取值 |
|---|---|
| 存在哪里 | 只在 Redis 里。绝不出现在 HTTP 响应中，不进数据库，不落盘。 |
| 有效期 | 签发起 24 小时。 |
| 输错次数 | 五次错误会作废当前令牌。 |
| 账号建好之后 | `POST /api/setup/init` 永久返回 `409`，令牌被删除。 |
| 重启 `api` | 会把令牌重新打印一遍。仍然有效的令牌是复用并重新播报，而不是替换——你手上已有的链接依然可用。 |

横幅里永远写着 `127.0.0.1:8000`，因为 api 容器的 `DTK_BIND_PORT` 在 compose 里被
钉死成 `8000`。如果你发布在别的地址，保留 `?token=` 部分，自己换掉主机和端口。

## 第 6 步 —— 走完初始化向导

打开那个 URL。向导有四步，而重点在第四步：一个只证明“能建账号”的部署，
不会告诉你代理、身份池和签名是否真的能用——那你就得在第一次真实 API 调用时才发现，
而那是最糟糕的时机。

| 步骤 | 做什么 | 可跳过 |
|---|---|---|
| 创建管理员 | 消耗令牌，创建第一个账号，角色为 `admin`。 | 否 |
| 配置代理 | 每行一个，然后探测。支持格式：`host:port`、`host:port:user:pass`、`user:pass@host:port`、`scheme://user:pass@host:port`。 | 是 |
| 铸造首个身份 | 每个身份排一个铸造任务，各需几秒。没有浏览器容器时不可用。 | 是 |
| 冒烟测试 | 对一条真实公开链接调用 `POST /api/v1/parse`，`?wait=20`，超时后退回轮询——和任何客户端走的是同一条路径。 | 是 |

管理员凭据的约束：

- 用户名必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$` —— 字母、数字、点、短横、
  下划线，3 到 64 个字符，且不能以标点开头。
- 密码：API 的下限是 8 位，向导要求 12 位。
- **没有邮件找回密码。** 恢复手段是一条 CLI 命令：
  `docker compose -p dtk -f docker/compose.yml exec api dtk user passwd <name>`。

不配代理时，所有身份共用服务器自己的出口地址。个人实例这样能跑，
但对任何有真实流量的场景都是错误选择——参见
[身份与代理](./06-identities-and-proxies.md)。

如果因为没有浏览器容器而跳过了铸造，现在就去**「身份池」→「导入 cookie」**。
导入器接受请求头、浏览器扩展导出的 JSON、Netscape 格式的 `cookies.txt`，
或者每行一个 `key=value`，并且会在存储之前先展示它读懂了什么。
User-Agent 要填同一个浏览器会话的，好让指纹和 cookie 对得上。
请用一个专门为此准备的账号：一份已登录的 cookie 等同于该账号的密码。

冒烟测试会显示平台、内容 id、耗时、request id 和解析出的 JSON。
这就是你的第一次成功解析。

## 第 7 步 —— 在控制台里解析一条链接

向导的冒烟测试只跑一次。要反复调用，请用侧边栏（*工具*分组下）的**「调试台」**。

1. 在左侧目录里选 `POST /api/v1/parse`。
2. 在 `url` 里填分享链接、作品原始 URL，或者平台 App 复制出来的整段剪贴板文本。
   短链（`v.douyin.com`、`vm.tiktok.com`）会自动跟随。
3. 把 `wait` 设成 `20` 之类的值。
4. 提交。

调试台的存在是为了让一个失效接口能在三分钟内定位：失败时它会给出稳定错误码、
request id、这次调用用的是哪个身份、该接口的熔断器此刻是否打开，以及身份池的状况——
正是这四个事实把“平台改了”和“我们没身份了”区分开。
它还会按表单的当前状态生成可直接复制的 `curl` 命令，这是进入下一步最快的方式。

## 第 8 步 —— 创建 API Key

控制台用的是会话 Cookie。程序应该用 API Key，因为 Key 带权限范围，而且可以单独撤销。

进入**「API Key」→「创建密钥」**。取一个在审计日志里一眼能认出来的名字，
只勾选它真正需要的权限——本文这一遍只要 `douyin:read` 和 `tiktok:read`。

| 权限范围 | 开放了什么 |
|---|---|
| `douyin:read` | 抖音的解析、作品、作者、评论接口 |
| `tiktok:read` | TikTok 的同类接口 |
| `archive:read` | 本实例已经采集到的内容，直接由本地存储返回 |
| `archive:export` | 一次请求遍历整个归档——这是唯一一个能把只读 Key 变成一份数据库副本的调用 |
| `media:read` | 服务器磁盘上存了哪些媒体、占了多少空间 |
| `media:write` | 发起下载、把某个下载置顶以免被清理、取消下载 |
| `identity:manage` | 铸造、导入、探测、退休身份。普通只读 Key 绝不该带上它 |
| `admin` | 全部权限，含设置与用户 |

在规划 Key 的权限布局之前，有两条规则值得先知道：

- 一个 Key 永远不能被授予其创建者本身不具备的权限范围。用 API Key 去创建另一个 Key
  无法提权。
- 即使 Key 属于管理员，权限范围依然生效。一个纯只读 Key 无论由谁创建，
  都碰不到身份管理。

`rate_limit` 是每分钟请求数，默认取实例的 `api.default_rate_limit_per_min`（120）。
它是防滥用手段。本项目没有计费、套餐或额度销售，这套文档里的 `rate_limit`
和 `quota` 从来都不指钱。

完整的 Key —— `dtk_<prefix>_<secret>` —— **只在创建后展示一次**。
服务端只保存前缀和整串的 SHA-256 哈希。任何人（包括管理员）都无法再读到它；
弄丢了的唯一办法就是撤销这个 Key，再建一个。

## 第 9 步 —— 用 `curl` 做同样的解析

默认情况下每个接口都需要 API Key 或控制台会话。用 `X-API-Key` 请求头认证：

```bash
API_KEY='dtk_...'   # 第 8 步拿到的 Key

curl -sS -X POST 'http://127.0.0.1:8000/api/v1/parse?wait=25' \
  -H "X-API-Key: ${API_KEY}" \
  -H 'content-type: application/json' \
  -d '{"url":"https://v.douyin.com/iRNBho6G/"}'
```

从平台取数据要在一个真实身份上发出一次真实的上游请求，可能耗时数秒，
所以数据接口默认是异步的。`?wait=` 把轮询这件事挪到服务端——它是给无法轮询的
客户端准备的便利，内部流程不因此改变。

| `?wait=` | 结果 |
|---|---|
| 不传或 `0` | 立刻返回 `202`，带 `task_id` 和 `state` |
| 不超过上限的值 | 连接会一直保持到任务结束。按时完成：`200` 带结果。没完成：`202` 且 `state: "running"` ——**这不是错误，什么也没丢** |
| 超过 `api.max_wait_seconds`（默认 30） | `400`，直接拒绝而不是悄悄截短 |
| 负数 | `400` |

成功返回长这样——每个响应都用同样的四键信封：

```json
{
  "success": true,
  "data": {
    "platform": "douyin",
    "content_id": "7300000000000000000",
    "kind": "video",
    "web_url": "https://www.douyin.com/video/7300000000000000000",
    "title": "…",
    "author": { "uid": "MS4wLjABAAAA…", "nickname": "…" },
    "stats": { "play_count": 0, "digg_count": 0, "comment_count": 0 },
    "media": { "covers": [], "video": { "url": "https://…" } }
  },
  "error": null,
  "meta": {
    "request_id": "0f0a…",
    "cached": false,
    "duration_ms": 1840,
    "task_id": "8c1e…"
  }
}
```

如果拿到的是 `202`，用它返回的 id 去轮询任务：

```bash
curl -sS -H "X-API-Key: ${API_KEY}" \
  'http://127.0.0.1:8000/api/v1/tasks/<task_id>'
```

失败用的是完全相同的信封，`data` 为 `null`，`error.code` 是**稳定且不翻译的**，
旁边的 message 才会本地化。请按 code 分支判断，永远不要解析 message。
任何接口（包括这份文档本身）加上 `?lang=zh` 就能拿到中文消息。

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "IDENTITY_POOL_EXHAUSTED",
    "message": "当前无可用身份，预计 10 秒后恢复。",
    "retry_after": 10
  },
  "meta": { "request_id": "0f0a…" }
}
```

这里展示的是加了 `?lang=zh` 之后的中文渲染结果。上面那条 curl 没有指定语言，
走的是实例默认的 `api.default_language`（`en`），所以它返回的 `message` 其实是英文；
两种情况下 `code` 都一样。

这个错误码说明第 3 步的账算到你头上了：没有可用身份。去铸造一个，或者导入一份 cookie。

## 跑通了吗？

逐条过一遍。每条对应的是不同的故障，第一条不通过的就指出了该查哪里。

```bash
# 1. 所有服务已启动，migrate 以 0 退出，api 和 worker 健康
#    加 -a 是因为 migrate 是一次性任务，此刻已经退出
docker compose -p dtk -f docker/compose.yml ps -a

# 2. API 能提供服务：postgres 和 redis 都可达
curl -sS http://127.0.0.1:8000/readyz

# 3. 控制台已提供
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/

# 4. 初始化已关闭，也就是管理员已存在
curl -sS http://127.0.0.1:8000/api/setup/status

# 5. 完整的六步自检，包含一次真实的端到端请求
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

| 检查项 | 应该看到什么 |
|---|---|
| `ps -a` | `postgres`、`redis`、`api`、`worker` 在运行；`api` 与两个存储健康；`migrate` 显示 `Exited (0)`。带浏览器 profile 时，`browser-rpc` 约一分钟后变健康。 |
| `/readyz` | `{"status":"ok","components":{"postgres":{"ok":true,…},"redis":{"ok":true,…}}}` 且 HTTP 200。返回 503 说明两个存储之一不可达。 |
| `/` | `200`。返回 `404` 说明控制台构建阶段没产出 `dist/`。 |
| `/api/setup/status` | `{"success":true,"data":{"initialized":true},…}` |
| `dtk diagnose` | 每一步都是 `PASS`，或者是你能理解的 `WARN`。输出已做脱敏，可以直接贴进 issue。 |
| 控制台 →「身份池」 | 至少有一个 `active` 身份。身份池为空是“请求没写错却仍然失败”最常见的原因。 |
| 控制台 →「调试台」 | `POST /api/v1/parse` 返回 `200`，且带 `data.content_id`。 |

`/healthz` 和 `/readyz` 是刻意分开的：`/healthz` 不碰任何依赖，所以数据库故障不会
让它失败；`/readyz` 会探测 Postgres 和 Redis。`browser-rpc` 被有意排除在就绪检查
之外——铸造不在请求路径上，所以没有它的实例是降级，不是未就绪。

## 如果出了问题

| 症状 | 原因 |
|---|---|
| `refusing to start: DTK_SECRET_KEY is not set`（退出码 `78`） | 仓库根目录缺 `.env`，或者写到了 `docker/` 里。按第 2 步的脚本重写。 |
| `redis: REDIS_PASSWORD is empty` | 同上，同解。 |
| api 日志里没有初始化横幅 | 已经存在账号——令牌只在 `users` 表为空时签发。改从 `/login` 登录。 |
| `SETUP_TOKEN_INVALID` | 令牌过期、已被用掉，或者因五次错误被作废。`docker compose -p dtk -f docker/compose.yml restart api` 会重新打印仍然有效的令牌，若旧的已失效则签发新的。 |
| `503 IDENTITY_POOL_EXHAUSTED` | 身份池里没有可用身份。铸造一个，或导入一份 cookie。见第 3 步。 |
| 铸造任务以 `501 NOT_CONFIGURED` 失败（mint 调用本身返回的是 `202`） | 没有浏览器容器。启动 `--profile browser` 并设置 `DTK_BROWSER_RPC_URL`，或者改用导入 cookie。 |
| `browser-rpc` 不健康，日志说 "not installed in this image" | 构建时没传 `CLOAKBROWSER_COMMIT`——这个值在构建时从 shell 读取，不从根目录 `.env` 读。 |
| 铸造总是超时 | 代理连不上目标平台。先单独测代理，再怀疑浏览器。 |
| 密码正确却返回 `RATE_LIMITED` | 按账号计数的登录限制是真的锁定：五次失败，锁十五分钟。等待，或者清掉计数器——见[故障排查](./14-troubleshooting.md)。 |

表里没有的情况，[故障排查](./14-troubleshooting.md)按症状组织，去那里找。

## 接下来读什么

- [安装与部署](./02-installation.md) —— 反向代理、下载器边车、备份、升级、扩容 worker。
- [核心概念](./04-concepts.md) —— 身份、调度器、签名、请求日志。调参之前先读它。
- [身份与代理](./06-identities-and-proxies.md) —— 身份池如何自动补充，以及补不上时怎么办。
- [控制台总览](./05-console-overview.md) —— 每个页面各管什么。
- [REST API 指南](./11-api.md) —— 完整接口面、分页、回调和错误码。在线参考在控制台的
  `/docs`，或者无需登录的 `/swagger` 与 `/redoc`。
- [用户与 API 密钥](./09-users-and-api-keys.md) —— 角色、权限范围，以及该给脚本什么。
- [MCP 与 AI 客户端](./12-mcp.md) —— `/mcp` 上共享同一套服务层，客户端配置见控制台的
  `/mcp-guide`。
- [安全](./15-security.md) —— 在把端口暴露出去之前读它。
