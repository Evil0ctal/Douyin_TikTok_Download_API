## What's new

v5 is a rewrite. It started from an empty branch and shares no code with v4.

v4's real problem was never a shortage of features — it was that the API would
die quietly and nobody would know. A cookie expires, a signature algorithm
changes, an endpoint gets rate-limited, and you find out when someone files an
issue. v5 puts "you can see it" and "it heals itself" ahead of features: it
mints its own guest identities with a headless browser, spreads requests across
them by health, trips a breaker per endpoint, and writes down what happened to
every single request.

### Upgrading

**There is no upgrade path from v4, and that is deliberate.** v5 is a different
schema, a different configuration model and a different container layout. Treat
it as a new install:

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
# write .env — see .env.example, or the Quick start in the README
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

v4 keeps running. Its code is on the [`v4` branch](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4)
and its image is still on Docker Hub as `evil0ctal/douyin_tiktok_download_api:V4.1.2`.
If you are staying on it, pin that tag rather than `latest` — `latest` is v5 now.

### Breaking changes

- **Everything.** Different endpoints, different response shape, different
  configuration. `config.yaml` is gone; runtime settings live in the database
  and are edited from the console. Nothing that talked to a v4 instance will
  talk to a v5 one without changes.
- **Bilibili is not carried over.** v4 supported it, v5 does not yet. It shares
  neither the signing nor the identity machinery with Douyin and TikTok, so the
  rewrite left it out.
- **Douyin follower and following lists are not exposed.** They are only served
  to a signed-in session, so registering endpoints that always return an empty
  page would have been dishonest. Import your own cookies and the rest sees more.

### Added

- **A self-maintaining identity pool.** A headless browser mints guest
  identities — cookies, fingerprint, proxy — and tops the pool up when usable
  ones run low. No more copying cookies out of a browser into a config file.
- **A scheduler that spreads the load.** Health tiers, quantised LRU rotation,
  one in-flight lock per identity, a token bucket per (identity, endpoint) and a
  circuit breaker per endpoint. No single identity carries the traffic.
- **A web console.** Identity pool, scheduler, playground, library, downloads,
  logs, diagnostics — in English and Chinese, both written by hand.
- **Asynchronous by default.** Endpoints answer `202` with a task id; add
  `?wait=` to make a call synchronous, or use a `callback_url` webhook.
- **MCP.** Point an agent at `/mcp` and it gets the same tools, the same
  identity pool and the same rate limits as the REST API.
- **A content archive.** Everything parsed is stored, so a post deleted upstream
  is still here. Media downloads land on your own disk.
- **Signing in pure Python.** a_bogus, X-Bogus, X-Gnarly and X-Dynosaur, with a
  browser fallback for when a platform changes one.
- **Demo mode.** Publish a shared read-only account and API key so anyone can
  try your instance. Turn it on in Settings; it is read-only, its requests are
  not written to the request log or the archive, and turning it off ends every
  demo session immediately.
- **API keys with scopes**, per-key rate limits, and four roles.

### Security

- Every stored credential — cookie jars, proxy URLs — is encrypted with the
  instance key. A database dump on its own does not carry them.
- Nothing ships a default password or key. The process refuses to start without
  `DTK_SECRET_KEY`.
- The published demo credentials are the single deliberate exception to
  "credentials are never readable", and they are readable only while demo mode
  is on.

---

## 本次更新

v5 是一次重写，从空分支起步，和 v4 不共享任何代码。

v4 最大的问题从来不是功能少，而是**接口会悄悄死掉，而你不知道**。Cookie 过期、
签名算法变更、某个接口被风控，通常都要等到有人来提 issue 才发现。v5 把「看得见」
和「能自愈」排在功能前面：用无头浏览器自己铸造游客身份，按健康度把请求摊开，
每个接口独立熔断，每一次请求都留下一条结构化记录。

### 升级方式

**从 v4 没有升级路径，这是刻意的。** v5 是另一套表结构、另一套配置模型、
另一套容器编排。请当作全新安装：

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
# 写 .env —— 见 .env.example，或自述文档的「快速开始」
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

v4 继续可用。代码保留在 [`v4` 分支](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4)，
镜像也还在，是 `evil0ctal/douyin_tiktok_download_api:V4.1.2`。
要留在 v4 上就固定这个版本号 tag，别用 `latest` —— `latest` 现在是 v5 了。

### 不兼容变更

- **全部。** 接口不同、响应结构不同、配置方式不同。`config.yaml` 没有了，
  运行时设置存在数据库里，在控制台改。任何对接过 v4 的东西都需要改造才能对接 v5。
- **哔哩哔哩没有移植过来。** v4 支持，v5 暂时没有。它和抖音 / TikTok 不共用
  签名和身份体系，重写时先放下了。
- **抖音的粉丝和关注列表没有开放。** 这两个接口只对已登录会话开放，
  注册一个永远返回空页的接口不诚实。导入你自己的登录 Cookie 之后，其余接口
  能看到的内容会更多。

### 新增

- **会自己维护的身份池。** 无头浏览器铸造游客身份（cookie + 指纹 + 代理），
  可用数不够时自己补。不用再从浏览器里抠 cookie 粘进配置文件。
- **把请求摊开的调度器。** 健康度分层、量化 LRU 轮换、每身份独占锁、
  每（身份，接口）令牌桶、接口级熔断。单个身份不会承担全部流量。
- **Web 控制台。** 身份池、调度器、调试台、资料库、下载、日志、诊断，
  中英双语，两种语言都是手写的。
- **默认异步。** 接口返回 `202` 和一个任务 ID；加 `?wait=` 可退回同步，
  也可以用 `callback_url` 回调。
- **MCP。** 把 AI 代理指向 `/mcp`，它拿到的工具、身份池和限流和 REST 接口是同一套。
- **内容归档。** 解析过的内容自动入库，平台删了这里还在。媒体下载存到你自己的磁盘。
- **纯 Python 的签名实现。** a_bogus、X-Bogus、X-Gnarly、X-Dynosaur，
  平台改算法时还有浏览器兜底。
- **演示模式。** 对外公开一个共用的只读账号和 API Key，任何人都能试用你的实例。
  在设置里开启；它是只读的，请求不写入请求日志和归档库，关闭后所有演示会话立即失效。
- **带作用域的 API Key**、每把 Key 独立限流，以及四种角色。

### 安全

- 所有存下来的凭据——cookie jar、代理地址——都用实例主密钥加密。
  单独拿到一份数据库导出是解不开的。
- 仓库里不带任何默认密码或密钥。没有 `DTK_SECRET_KEY` 进程直接拒绝启动。
- 对外公开的演示凭据是「凭据永远不可读取」这条规则唯一一处刻意的例外，
  而且只在演示模式开着时可读。

---

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/commits/v5.0.0

**Docker**: `evil0ctal/douyin_tiktok_download_api:5.0.0`
