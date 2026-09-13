# REST API 指南

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** ——
> 自部署的抖音 / TikTok 数据接口服务：REST、MCP 和 Web 控制台，身份池自维护。
> [文档首页](../README.zh-CN.md) · [English](../en/11-api.md)

本文讲的是如何从程序里**调用** HTTP API：怎么认证、统一信封长什么样、异步任务模型怎么工作、怎么翻页、以及你的代码必须处理哪些错误。读完之后，你应该能写出一个客户端：提交任务、取回结果，并在实例开始拒绝你时正确地退避。

它是生成式接口参考的补充，而不是重复。`/swagger`、`/redoc` 和 `/openapi.json` 列出了每一个接口、每一个参数和每一条约束，而且它们是由处理请求的同一份代码生成的，永远不会过期。本文解释的是接口清单说不清的部分：一个 `202` 到底意味着什么、什么时候该重试、以及某个参数会让你付出什么代价。

## 本文涵盖的内容

| 位置 | 提供什么 | 是否需要凭据 |
| --- | --- | --- |
| `/swagger` | 覆盖全部接口的 Swagger UI，带 "Try it out" | 查看无需；真正调用需要 key |
| `/redoc` | 同一份文档，偏阅读的排版 | 查看无需 |
| `/openapi.json` | OpenAPI 文档本身，用于生成客户端代码 | 无需 |
| `/docs` | 控制台自带的接口参考页，带主题、嵌在控制台外壳里 | 查看无需 |
| 本文 | 如何在程序里用好上面这些 | — |

`/docs` 同样不需要会话：未登录时它会以控制台外壳的未登录状态渲染，并读取与其余几项相同的公开 `/openapi.json`。

以上四个都支持 `?lang=`（见[响应语言](#响应语言)），所以 `/swagger?lang=zh` 会渲染中文文档。

## Base URL 与版本

compose 栈默认把 API 发布在 `127.0.0.1:8000`（用 `.env` 里的 `DTK_BIND_HOST` 和 `DTK_BIND_PORT` 修改，见[安装与部署](./02-installation.md)）。如果你还没有跑起来的实例、也还没有 key，[快速开始](./01-quickstart.md)大约十分钟就能把两样都准备好。下面所有示例都基于：

```bash
export DTK_BASE_URL=http://127.0.0.1:8000
export DTK_API_KEY=dtk_0a1b2c3d4e5f_REPLACE_WITH_YOUR_KEY
```

路径布局：

| 前缀 | 内容 |
| --- | --- |
| `/api/v1/…` | 程序调用的一切：内容、任务、工具、归档、下载、管理 |
| `/api/setup/…` | 仅用于首次初始化；出于必要对匿名调用方开放，由一次性 setup 令牌把守，并且永远不能被写进 `api.public_endpoints` |
| `/healthz`、`/readyz` | 进程探针，在版本化接口之外，也不走信封 |
| `/mcp/` | MCP 端点，末尾的斜杠要保留——`/mcp` 会以 `307 Temporary Redirect` 跳到它。见 [MCP 与 AI 客户端](./12-mcp.md) |

路径里的 `v1` 是这份契约的版本号。其中还有两样东西同样被当作契约、只增不改也不改名：**错误码**和**信封的键**。`data` 和 `meta` 里将来可能出现新字段，所以解析时请宽松一些——只读你需要的键，忽略其余的。

当前运行版本由 `GET /api/v1/system/status` 给出，返回 `version`、`commit`、`uptime_seconds`、`settings_version`、各组件健康状况、身份池统计和存储占用。它需要凭据，因为暴露组件版本号和行数已经超出了一个匿名探针该知道的范围。

## 认证

除非运营方主动开放，否则每个接口都需要凭据。有少数几个路由天生匿名、且无法被关闭——登录、登出、`/api/setup` 下的两个路由，以及 `GET /api/v1/ios/shortcut`（快捷指令在还没有地方存放 API key 的时候就要问它）。完整清单见[从未上锁的路由](./09-users-and-api-keys.md#从未上锁的路由)；`/healthz` 和 `/readyz` 同样不需要认证，而且根本不在 API 文档里。程序用 API key 认证，两种请求头形式**任选其一**——两者等价，同时存在时先读 `Authorization`：

```bash
curl -sS "$DTK_BASE_URL/api/v1/auth/me" -H "Authorization: Bearer $DTK_API_KEY"
curl -sS "$DTK_BASE_URL/api/v1/auth/me" -H "X-API-Key: $DTK_API_KEY"
```

`GET /api/v1/auth/me` 是检查一个 key 是否有效、以及它能做什么的最省事的办法：它返回账号、角色、该凭据携带的权限范围、`via`（`api_key` 或 `session`），以及这个 key 自己的 `rate_limit_per_min`。

还有第三种形式，但它不是给程序用的：控制台用 `POST /api/v1/auth/login` 登录并拿到 `dtk_session` cookie。程序请用 key——key 带权限范围、可以单独撤销，也不会随浏览器会话过期。

### Key

一个 key 形如 `dtk_<12 位十六进制>_<随机串>`。它由 `POST /api/v1/admin/api-keys` 生成，并且**只在那一次响应里出现一次**；服务端只保存前缀（用于展示）和 SHA-256 摘要（用于校验），所以谁也读不回来——管理员也不行。撤销在下一次请求即刻生效，因为认证每次都会读那一行。参见[用户与 API 密钥](./09-users-and-api-keys.md)。

### 权限范围（scope）

| 权限范围 | 能到达 |
| --- | --- |
| `douyin:read` | 抖音内容接口，以及对抖音链接的 `/parse` |
| `tiktok:read` | TikTok 内容接口，以及对 TikTok 链接的 `/parse` |
| `archive:read` | `GET /api/v1/archive…`——本实例已经存下来的内容 |
| `archive:export` | `GET /api/v1/archive/export`——一次调用拿走整个集合 |
| `media:read` | 下载记录与已存文件 |
| `media:write` | 发起、置顶和取消下载 |
| `identity:manage` | 身份池，以及 `identity`、`explain` 这两个请求参数 |
| `admin` | 全部 |

有两条规则值得单独说，因为它们常让人意外：

- **即使 key 的所有者是管理员，key 依然只受自身权限范围的约束。** 在自部署实例上几乎所有 key 都属于管理员账号；一个纯 `douyin:read` 的 key 仍然够不到身份管理，也够不到 `archive:export`。
- **读取任务结果所需的权限范围，与创建它时相同。** `GET /api/v1/tasks/{task_id}` 会按任务提交时的接口去校验权限范围，所以低权限 key 拿到一个任务 ID 也读不出高权限的结果。

部分操作在权限范围之外还要求**角色**（`demo` < `viewer` < `operator` < `admin`）——`identity` 和 `explain` 都至少需要 `operator`。角色属于凭据所归属的账号。`?proxy=` 不在其列：它完全不做权限范围或角色检查，只由 `security.request_proxy` 这个设置把守，见下文的 [`?proxy=<url>`](#proxyurl)。

### 被开放的接口

运营方可以在 `api.public_endpoints` 里逐条列出接口（写作 `GET /api/v1/{platform}/video`，与 API 文档里的路径写法完全一致），这些接口就不再需要凭据。admin、auth 和 setup 路径无论设置怎么写都永远不能被开放。匿名调用方只带 `douyin:read` 和 `tiktok:read`，并且按客户端地址而不是按 key 计量。

发送一个**错误的**凭据和不发凭据不是一回事：即使在已开放的接口上，被拒绝的 key 也会得到 `UNAUTHENTICATED`，而不会被悄悄降级成匿名。这样「你的 key 过期了」会变成一个你看得见的错误，而不是「key 能用，只是看到的东西变少了」。

## 统一响应信封

每一个 JSON 响应——无论成功还是失败——都有同样的四个顶层键。

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": { "request_id": "b6f0f2c6-4a5f-4a0e-9a1f-2b7c2b6f1f21" }
}
```

| 键 | 含义 |
| --- | --- |
| `success` | `true` 或 `false`。请按它分支，而不是只看 HTTP 状态码 |
| `data` | 接口自己的载荷。`success` 为 `false` 时恒为 `null` |
| `error` | 成功时为 `null`。否则是 `{code, message}`，另可带 `retry_after` 和 `details` |
| `meta` | 恒带 `request_id`。可能带 `cached`、`duration_ms`、`cursor` 以及接口特有的附加字段 |

**请按 `error.code` 分支，永远不要按 `error.message`。** code 是稳定枚举、永不翻译；message 会按调用方的语言渲染，是写给人看的。

### 一个成功的例子

提交一个链接会返回 `202` 和一个任务 ID：

```bash
curl -sS -X POST "$DTK_BASE_URL/api/v1/parse" \
  -H "X-API-Key: $DTK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.douyin.com/video/7123456789012345678"}'
```

```json
{
  "success": true,
  "data": { "task_id": "0f2f1b7c-3f9e-4b8a-9a11-0a6d2b0e51c3", "state": "queued" },
  "error": null,
  "meta": { "request_id": "b6f0f2c6-4a5f-4a0e-9a1f-2b7c2b6f1f21" }
}
```

### 一个失败的例子

发一个不在白名单里的 URL，同样的信封会装着错误，HTTP 状态码是 `400`：

```bash
curl -sS -X POST "$DTK_BASE_URL/api/v1/parse" \
  -H "X-API-Key: $DTK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://evil.example/video/1"}'
```

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "INVALID_URL",
    "message": "The URL was not recognized as a supported Douyin or TikTok link.",
    "details": { "reason": "host_not_allowed" }
  },
  "meta": { "request_id": "1d5b1a7e-90c7-4f2e-9b3c-6b2f0f9a8d44" }
}
```

（`message` 这里是英文，因为这条命令没有指定语言，走的是实例默认的 `en`；加上 `?lang=zh` 就会渲染成中文。）

`details` 按名字组织，形状随 code 而变——出错的字段、拒绝的接口、没找到的 ID。它里面永远不会有凭据。

### 关联排查

每个响应都带两个可以记进日志的头：

| 响应头 | 含义 |
| --- | --- |
| `X-Request-ID` | 关联 ID，同时会写进请求日志 |
| `X-Response-Time-Ms` | 本进程在这个请求上花的毫秒数 |

报 bug 时请附上 `X-Request-ID`，运营方能在控制台的日志页找到对应那一行。`meta.request_id` 通常是同一个值——但有一个例外值得知道：由已完成任务渲染出来的结果（即等到了结果的 `?wait=` 调用），它的 `meta.request_id` 是 **worker 自己的**抓取请求 ID，因为 worker 的元数据被合并进了这里。响应头则始终是 HTTP 关联 ID。

### Content type

POST 请求体必须以 `application/json` 发送。裸的 `curl -d '{…}'` 发的是 `application/x-www-form-urlencoded`，请求体解析不出来——本文每个示例都带 `-H 'Content-Type: application/json'` 就是这个原因。解析失败会返回 `INVALID_PARAM`，`details.fields` 里给出字段路径和原因；你发的值故意不会被回显，因为对一个解析失败的请求体来说，那个「值」就是整个请求。

请求体上限为 **1 MiB**。超出会得到 HTTP `413`，code 为 `INVALID_PARAM`，并带上 `details.limit_bytes`。

## 异步模型

从平台抓一次数据，要真实消耗一次上游请求和一个池化身份，而且可能要好几秒。所以**数据类接口默认是异步的**：先校验、把工作排队，然后立刻返回 `202` 和一个任务 ID。API 容器本身从不与平台通信。

```
POST /api/v1/parse            -> 202 {"task_id": "...", "state": "queued"}
GET  /api/v1/tasks/{task_id}  -> 200 {"state": "done", "data": {...}}
```

取结果有三种方式，区别只在于「由谁来等」。

### 1. 轮询任务

反复 `GET /api/v1/tasks/{task_id}`，直到 `state` 为 `done` 或 `failed`。它可以放心轮询、也可以放心重复调用：任务不变，答案就不变。载荷如下：

| 字段 | 何时出现 | 含义 |
| --- | --- | --- |
| `task_id` | 总是 | 你轮询的那个 ID |
| `state` | 总是 | `queued`、`running`、`done` 或 `failed` |
| `endpoint` | 总是 | 提交的是什么，例如 `parse` 或 `douyin.content_detail` |
| `created_at`、`finished_at` | 总是 | ISO-8601；未结束前 `finished_at` 为 `null` |
| `data` | `done` 时 | **结果载荷，嵌在信封的 `data` 里面** |
| `result_meta` | 结束后 | `cached`、`duration_ms`、`endpoint`、`platform`、`cursor`，以及需要权限才可见的附加项 |
| `error` | `failed` 时 | `{code, message, retryable}`，另可带 `retry_after` 和 `details` |

注意这层嵌套：任务完成后结果在 `body.data.data`，不是 `body.data`。另外注意：**失败的任务依然是 HTTP 200、`success: true`**——读取任务这个 HTTP 调用成功了；失败在它里面，在 `data.state` 和 `data.error` 里。这与下面的 `?wait=` 恰好相反，这个差别很容易绊倒人。

任务存下来的 `error` 对象带一个明确的 `retryable` 布尔值，这样读结果的 agent 不必自己背下那张「不可重试」的表。

**结果会过期。** 载荷保留 `retention.task_result_hours`（默认 **24** 小时）。之后行还在、状态和时间戳还在，但载荷被清空，查询会返回 `TASK_NOT_FOUND`。这意味着重新提交，而不是继续轮询。

### 2. 让服务端替你等 —— `?wait=`

给任何提交类接口加上 `?wait=10`，连接就会被保持住直到任务有结果，最多等这么多秒。**这就是把调用变成同步的方式**，它是为那些根本没法轮询的客户端准备的：iOS 快捷指令、一行 shell 命令、表格软件。

| 结果 | 状态码 | 响应体 |
| --- | --- | --- |
| 在时限内完成 | `200` | 结果在 `data` 里，与任务接口返回的完全一致 |
| 你等待期间任务失败了 | 该错误自己的状态码（如 `503`） | 正常的失败信封，带该错误的 code |
| 时限内没完成 | `202` | `{"task_id": "...", "state": "running"}` |
| 超过本实例上限 | `400` | `INVALID_PARAM`，带 `details.max` |
| 省略，或传 `0` | `202` | 立刻返回，与不等待相同 |
| 传负数 | `400` | `INVALID_PARAM` |

**等待超时后返回的 `202` 不是错误，也没有丢任何东西。** 工作还在跑，稍后用同一个任务 ID 就能取到。把这个提前返回的 `202` 当成失败的客户端，会把已经排好队的工作再提交一遍，白白多花一次身份，最后拿到同样的答案。

上限是 `api.max_wait_seconds`，默认 **30 秒**。超过它的值是被**拒绝**而不是悄悄缩短——这是刻意的，因为一个要求阻塞五分钟的调用方必须知道自己做不到，否则它会把提前返回的 `202` 读成失败。当前上限会以 `maximum` 的形式发布在 OpenAPI 文档的该参数上。

内部机制不因此改变：无论哪种方式，工作都走同一个队列。`?wait=` 决定的只是谁来持有这个连接。对批处理作业、或任何有自己事件循环的程序来说它都是错误选择——你是在用一条长期占用的 HTTP 连接，换掉自己写一个循环。

### 3. 让服务端回调你 —— `callback_url`

`POST /api/v1/parse` 和 `POST /api/v1/tasks/batch` 的请求体接受 `callback_url`。任务完成后实例会往那里 POST 一条通知，于是既不用轮询也不用阻塞。

这是一个目的地由调用方指定的出站请求——最典型的 SSRF 形态——所以它被层层设限：

- 除非管理员打开 `security.enable_task_webhook`（默认**关闭**），否则一律拒绝。在关闭的实例上传这个参数，会得到针对 `callback_url` 的 `INVALID_PARAM`。
- URL 必须是 `https`，主机不能是回环、私有或链路本地地址。提交时校验一次，**投递时再校验一次**，因为设置可能被关掉，而排队中的任务可能活得比「被接受的那一刻」更久。
- 不跟随重定向，且校验证书。

通知说的是「发生了什么」，不是「抓到了什么」——结果可能有好几兆，而把它 POST 给第三方是一个没人会靠在输入框里填个 URL 来做出的数据流决策：

```json
{
  "event": "task.completed",
  "task_id": "0f2f1b7c-3f9e-4b8a-9a11-0a6d2b0e51c3",
  "endpoint": "parse",
  "state": "done",
  "sent_at": "2026-09-10T04:15:12.883921+00:00"
}
```

失败时 `"event"` 为 `"task.failed"`，并多一个 `error`，里面是 code 和被截断的 message。真正的结果请用 `GET /api/v1/tasks/{task_id}` 去取。

| 属性 | 值 |
| --- | --- |
| `X-Dtk-Event` 请求头 | `task.completed` 或 `task.failed` |
| `X-Dtk-Signature` 请求头 | 配置了 `security.webhook_secret` 时为 `sha256=<hex>`，对发出的原始字节做 HMAC-SHA256 |
| 超时 | 每次尝试 10 秒 |
| 尝试次数 | 3 次，退避 2 秒、8 秒 |
| 提前放弃的情况 | 除 `429` 外的任何 `4xx` |

校验签名时请对**原始响应体字节**做，而不是对反序列化后再序列化的对象做——键顺序和分隔符会不同，本来正确的校验也会失败。没有配置密钥的话，接收方无从区分一条真通知和任何猜到了 URL 的人。

投递永远不影响任务本身。一个宕机、缓慢或恶意的 webhook 端点不能把一次成功的抓取变成失败的任务；每一次投递失败都只记日志然后咽掉。

### 事件流

`GET /api/v1/tasks/{task_id}/events` 是一条 server-sent events 流，控制台用它替代同时轮询大量行。

| 事件 | 载荷 |
| --- | --- |
| `state` | 不含结果的任务载荷，状态每次变化时发送 |
| `result` | 任务变为 `done` 或 `failed` 后的完整载荷 |
| `end` | `{task_id}`，紧跟在 `result` 之后 |
| `timeout` | 先到达截止时间时发送 `{task_id, state}` |
| `error` | 行消失时发送 `{code: "TASK_NOT_FOUND", message}` |

`?timeout=` 把流限制在 1 到 **300** 秒之间，默认 300；每约 15 秒发一个注释帧，免得中间的代理掐掉一条安静的连接。鉴权在流打开之前完成。需要特权的 `explain` 块**不会**出现在这条流上——请用普通的 `GET` 取一次，那里的权限范围检查就挨着读取本身。

### 批量提交

`POST /api/v1/tasks/batch` 最多接受 **50** 条，且总是返回 `202`。提交是批量的，跟踪不是——每一条都有自己的任务 ID 和自己的命运，所以一条坏链接不会把整个请求糊成一个错误：

```json
{
  "success": true,
  "data": {
    "items": [
      { "url": "https://v.douyin.com/abc123/", "task_id": "0f2f…", "state": "queued" },
      { "url": "https://evil.example/x", "task_id": null,
        "error": { "code": "INVALID_URL",
                   "message": "The URL was not recognized as a supported Douyin or TikTok link.",
                   "details": { "reason": "host_not_allowed" } } }
    ],
    "submitted": 1,
    "rejected": 1
  },
  "error": null,
  "meta": { "request_id": "…" }
}
```

逐条的错误也会按你请求的语言渲染，和这个 API 返回的其他错误一样。`/tasks/batch` 不接受 `?wait=`：请逐个轮询任务 ID，或者提供 `callback_url`。

### 队列满的时候

一旦 Redis 队列达到 `sched.queue_max`（默认 **500**），提交会被拒绝而不是继续排队：`503`，code 为 `QUEUE_FULL`，带 `Retry-After` 响应头，`details.queued` 给出当前深度。`retry_after` 取 `sched.max_wait_seconds`（默认 **10**）。让调用方干等着，比直接告诉它稍后再来更糟。

有两个刻意的例外：能合并到**已在队列中**的相同任务上的请求会被放行（它并不增加积压）；运营方手动触发的维护作业豁免——因为队列一深就拒绝跑自检，等于恰好在最需要这个工具的时候把它收走。媒体下载**不**豁免，否则某个运营方的批量下载会把实例上所有读请求饿死。

## 分页

列表类接口用**不透明游标**翻页。你把上一页给你的东西原样发回去，别的什么都不用做。

| 参数 | 出现在 | 默认值 | 上限 |
| --- | --- | --- | --- |
| `cursor` | 平台列表、归档列表 | 首页 | 512 字符 |
| `count` | 平台列表 | 20 | 50 |
| `limit` | `GET /api/v1/archive` | 50 | 200 |

平台列表的结果会在两个地方带上游标，因为有两类读者想要两种形状：

```json
{
  "success": true,
  "data": { "items": [], "cursor": "1757462400000", "has_more": true },
  "error": null,
  "meta": {
    "request_id": "…",
    "cached": false,
    "duration_ms": 812,
    "cursor": { "next": "1757462400000", "has_more": true },
    "task_id": "…",
    "endpoint": "douyin.author_posts",
    "platform": "douyin"
  }
}
```

如果你走的是轮询而不是等待，同样的值在 `data.data.cursor` 和 `data.result_meta.cursor.next`。

规则：

- **游标是不透明的。** 抖音按毫秒级 `max_cursor` 时间戳翻页，TikTok 按偏移量翻页；两者都被字符串化进同一个字段，调用方不需要知道是哪一种。不要解析它、不要给它加一、也不要自己构造。
- **没有游标、或 `has_more: false`，就是最后一页。** 到此为止，不要再把上一个游标发一遍。
- 游标属于产生它的那次查询。翻到一半改 `count` 或任何过滤条件、还复用旧游标，属于未定义行为。

`count` 超过上限会在校验阶段被拒绝，而不是被悄悄裁掉（OpenAPI schema 里声明了 `maximum: 50`）。想要更多的调用方应该翻页；想一次拿走全部的调用方，正是应该被放慢的那个。

也不是所有地方都用游标。`GET /api/v1/downloads` 和各管理列表用 `limit` + `offset` 并返回 `total`（默认 100，上限 500），因为它们读的是本地数据库；`GET /api/v1/admin/logs/requests` 用一个有界的 `minutes` 窗口加 `limit`，所以不存在任何一组参数能不带时间窗就打到日志表上。

## 响应语言

错误码在任何语言下都一样，旁边那句话不一样。单次请求的解析顺序：

1. `?lang=` 查询参数——`en` 或 `zh`
2. `Accept-Language` 请求头——按主子标签匹配，所以 `zh-CN`、`zh-Hans`、`zh-TW` 都归到 `zh`；`*` 归到配置的默认值
3. `api.default_language`（默认 `en`）

```bash
curl -sS "$DTK_BASE_URL/api/v1/parse?lang=zh" -X POST \
  -H "X-API-Key: $DTK_API_KEY" -H 'Content-Type: application/json' \
  -d '{"url": "not a link"}'
```

不支持的 `?lang=` 值会被**忽略**而不是报错，协商继续走请求头——所以 `?lang=fr` 仍然会尊重 `Accept-Language: zh`。两种语言都对不上的，回落到英文，而不是用一种本构建没有的语言半吊子地服务你。

这条规则同样适用于 OpenAPI 文档（`/openapi.json?lang=zh`、`/swagger?lang=zh`），也适用于任务里存下来的错误：worker 存的是 code 和它的参数，句子是在你读取的时候才拼出来的，所以同一个失败任务既能正确回答中文控制台，也能正确回答英文 agent。

## 错误

每一次失败都是同一个信封，`success: false`。请按 `error.code` 分支。

| Code | HTTP | 可重试 | 含义 |
| --- | --- | --- | --- |
| `INVALID_URL` | 400 | 否 | 不是受支持的抖音或 TikTok 链接，或主机不在白名单里 |
| `UNSUPPORTED_CONTENT` | 400 | 否 | 该平台不提供这个操作；`details.supported` 列出哪些平台提供 |
| `INVALID_PARAM` | 400 | 否 | 某个参数不对，`details` 指出字段。请求体超过 1 MiB 上限也用它（HTTP 状态为 413） |
| `UNAUTHENTICATED` | 401 | 否 | 没有凭据，或凭据被拒绝 |
| `FORBIDDEN_SCOPE` | 403 | 否 | 凭据没有权限；`details` 两边都给：`required_scopes` 或 `required_roles`，以及 `have_role` / `have_scopes` / `via` |
| `CONTENT_PRIVATE` | 403 | 否 | 私密内容，或已被作者删除 |
| `NOT_FOUND` | 404 | 否 | 没有这个资源 |
| `TASK_NOT_FOUND` | 404 | **是** | 没有这个任务，或结果已过期。就这个标志的含义而言它可重试——但过期的结果不会因为重试而回来，请重新提交 |
| `METHOD_NOT_ALLOWED` | 405 | 否 | 这个路径不支持该方法；`Allow` 头列出支持的方法 |
| `SETUP_ALREADY_DONE` | 409 | 否 | 本实例已经初始化过了 |
| `CANCELLED` | 409 | 否 | 有人主动取消了这个任务 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | 否 | 请求体的媒体类型不是本接口能读的 |
| `RATE_LIMITED` | 429 | **是** | 请求过于频繁；请遵守 `retry_after` |
| `SETUP_TOKEN_INVALID` | 403 | 否 | 首次初始化令牌错误或已用掉 |
| `NOT_CONFIGURED` | 501 | 否 | 本部署从来就没有这个可选组件 |
| `INTERNAL` | 500 | **是** | 意料之外；报 bug 时请附上 `X-Request-ID` |
| `UPSTREAM_RISK_CONTROL` | 502 | **是** | 平台判定该请求为自动化流量，相关身份进入冷却 |
| `UPSTREAM_CHANGED` | 502 | 否 | 平台响应结构与解析器不再匹配，`details.path` 指出字段。请反馈 |
| `SIGNING_FAILED` | 502 | **是** | 签名失败，算法可能已失效 |
| `IDENTITY_POOL_EXHAUSTED` | 503 | **是** | 没有可用身份；`retry_after` 是恢复时间的估计 |
| `ENDPOINT_CIRCUIT_OPEN` | 503 | **是** | 该接口因连续失败被熔断暂停 |
| `QUEUE_FULL` | 503 | **是** | 队列已达上限 |
| `DOWNLOADER_UNAVAILABLE` | 503 | **是** | 媒体边车进程在跑但没有响应 |

`NOT_CONFIGURED` 和 `DOWNLOADER_UNAVAILABLE` 看着像，其实不是一回事：前者说明这个部署压根就没装这个组件，等多久都没用；后者说明一个确实存在的服务挂了。

### `retry_after`

会自行恢复的错误码，会在 `error` 里带 `retry_after`（秒），**同时**在标准的 `Retry-After` 响应头里带同一个值。至少等这么久。你实际会遇到的是 `RATE_LIMITED`、`QUEUE_FULL`、`IDENTITY_POOL_EXHAUSTED`、`ENDPOINT_CIRCUIT_OPEN` 和 `UPSTREAM_RISK_CONTROL`。

### `retryable`

`retryable` 布尔值出现在**任务存下来的错误**里（`GET /api/v1/tasks/{task_id}` → `data.error.retryable`），以及 OpenAPI 的错误 schema 里。顶层信封的错误只带 code，而上面那张表就是这个 code 的含义。按 code 分支的客户端不需要别的；而读任务结果的 agent 能直接拿到这个标志，不必自己背表。

### 客户端必须处理的错误码

其余的大体上可以「记日志然后停下」，但下面这四种决策必须写进你的代码：

1. **退避后重试**：`RATE_LIMITED`、`QUEUE_FULL`、`IDENTITY_POOL_EXHAUSTED`、`ENDPOINT_CIRCUIT_OPEN`、`UPSTREAM_RISK_CONTROL`、`SIGNING_FAILED`、`INTERNAL`、`DOWNLOADER_UNAVAILABLE`。有 `retry_after` 就遵守它，没有就用指数退避。
2. **绝不重发同一个请求**：`INVALID_URL`、`UNSUPPORTED_CONTENT`、`INVALID_PARAM`、`NOT_FOUND`、`CONTENT_PRIVATE`、`METHOD_NOT_ALLOWED`、`UNSUPPORTED_MEDIA_TYPE`、`CANCELLED`、`NOT_CONFIGURED`、`UPSTREAM_CHANGED`。在这些上面打转，只是烧身份去换同一句话。
3. **修凭据**：`UNAUTHENTICATED`、`FORBIDDEN_SCOPE`。`details` 把两边都写出来了：`required_scopes` 或 `required_roles` 是接口要什么，`have_role`、`have_scopes`、`via` 是你发过去的是什么。先看 `via`——它说明这个调用方是按 scope 判定（`api_key`）还是按角色判定（`session`），而管理员的 key 同样受它自己的 scope 约束。
4. **重新提交工作**：`TASK_NOT_FOUND`。结果窗口已经过去，这个任务 ID 死了。

## 速率限制

按凭据计的一分钟固定窗口。它是防滥用，不是计量也不是计费：唯一目的是别让一个失控脚本把身份池抽干。

| 响应头 | 含义 |
| --- | --- |
| `X-RateLimit-Limit` | 当前窗口允许的请求数 |
| `X-RateLimit-Remaining` | 还剩多少 |
| `X-RateLimit-Reset` | 窗口翻页的 Unix 时间戳（秒） |

限额取 key 创建时设定的 `rate_limit`（1 到 100000 之间），否则取 `api.default_rate_limit_per_min`（默认 **120**）。如果这个实例默认值本身被设成 0 或更小，那么所有没有单独限额的调用方都不再计数，这三个头也不会出现。超限返回 `429`，code 为 `RATE_LIMITED`，带 `details.limit`、`error.retry_after` 和 `Retry-After` 头。

桶的键：带 key 时按 API key，控制台会话按用户，已开放接口上的匿名调用按客户端地址。最后这一种在你依赖它之前有个必须知道的注意事项：在 Docker 发布端口的用户态代理后面，所有请求看起来都来自网桥网关，所以除非运营方用 `DTK_FORWARDED_ALLOW_IPS` 声明了反向代理，匿名桶会退化成整个互联网共用一个桶。对一个防滥用计数器来说，往更严的方向退化是对的，但这意味着一个忙碌的匿名调用方可能把别人挡在门外。参见[安全](./15-security.md)。

请读这些头并在撞上 `429` 之前主动放慢，而不是靠试探去找上限。

## 重复调用：任务合并、缓存与 `?refresh=`

有两套互相独立的机制让重复请求变便宜，而且都是默认开启的。

**任务合并（coalescing）。** 短时间内到达的两个完全相同的请求会被合并到同一个任务上，而不是跑两遍——一百个调用方问同一个视频，只花身份池一次上游请求。这个占位声明存活 90 秒。已经**失败**的任务永远不会被合并进来：在剩余窗口里重放一次失败，会掩盖掉一次本可能成功的重试。

**响应缓存。** 整形后的结果会按请求内容缓存不同时长：

| 设置 | 默认值 | 适用于 |
| --- | --- | --- |
| `cache.content_ttl` | 1800 秒（30 分钟） | 单条作品 |
| `cache.author_ttl` | 900 秒（15 分钟） | 作者资料 |
| `cache.list_ttl` | 300 秒（5 分钟） | 任何分页内容 |

`meta.cached` 告诉你拿到的是哪一种。条目会自行过期，而且 Redis 的上限设在容器限制之下，所以缓存不会无限增长。

**`?refresh=true` 会同时关掉这两者。** 它忽略任何缓存的或在途的答案，重新去上游要一次。新答案仍然会被写入缓存——「别读缓存」和「别留下这份」是两个不同的请求，而你只提了第一个。它要真的花掉一个身份和一次上游请求，所以它是用来确认「有没有变」的，不是每次调用都该带的。

这两套机制也解释了为什么同一个请求的两种写法会合并：`?url=https://www.douyin.com/video/7123…` 和 `?aweme_id=7123…` 会被提取成同样的参数，拿到同一个任务 ID。

## `?include_raw=`

在归一化结果之外，附上平台自己那份未经处理的原始载荷。默认关闭，开之前值得先了解：

- 单条作品就已经很大——一条作品的原始载荷有几百 KB。
- 在**分页**结果里是逐条附带的，所以它会成倍放大响应体，以及任何存储它的东西。
- 它是缓存键的一部分，所以同一个东西的 raw 与非 raw 请求，是两条缓存记录、两次上游调用。

在 `POST /api/v1/parse` 和 `POST /api/v1/tasks/batch` 的每一条 item 上它是请求体字段；在 `GET` 接口上它是查询参数。

## `?identity=` 与 `?proxy=`

这两个改变的是请求**怎么发出去**，而不是它在问什么，并且都有权限门槛。

### `?identity=<uuid>`

用指定的那一个身份发请求，不用别的。它存在的场景是：你从自己已登录的浏览器里导入的一份 Cookie 罐——那些内容只有那个会话看得见，所以换一个身份不是把答案变差，而是把问题换掉了。

- 需要 `identity:manage` 权限范围**以及**至少 `operator` 角色。
- 提交时就会检查存在性、是否已退休、平台是否匹配，所以打错的 uuid 会立刻得到一个指明字段的 `400` 或 `404`，而不是排队、执行、然后失败的任务。已退休的身份会被明确拒绝——退休会抹掉密文，已经没有 jar 可以拿来签名了。
- 被指定身份的请求**既不读也不写响应缓存**，也永远不会被合并到未指定身份的任务上。
- 它只有**一次**传输尝试，而不是三次。重试之所以有意义，是因为下一次会落到另一个身份、走另一个出口；指定了身份，三次尝试只会把这一个身份的令牌桶抽干。
- 身份 ID 会在结果元数据里以 `identity_id` 回显——而且只在你自己指定过的时候。

参见[身份与代理](./06-identities-and-proxies.md)。

### `?proxy=<url>`

让上游请求走**你**提供的出口，替换掉该身份自己的出口。

- 除非管理员把 `security.request_proxy` 设为 `public` 或 `any`，否则一律拒绝。默认是 `deny`，而且无法识别的值也按 `deny` 处理——配置里的一个笔误不该成为打开网络的那件事。
- `public` 只接受公网可路由的目的地；`any` 连回环和私有网段也接受，只有在每一个持有 API key 的人都已经被信任可以访问该实例所在网络时才说得通。
- 协议：`http`、`https`、`socks5`、`socks5h`。最长 512 字符。请写成完整 URL，例如 `http://host:port`。
- 拒绝时返回 `INVALID_PARAM`，`details.reason` 是 `request_proxy_disabled`、`too_long`、`scheme_missing`、`scheme_not_supported`、`host_missing`、`port_invalid`、`host_not_public` 之一。你发的值永远不会被回显或写日志，因为一个被拒的代理 URL 恰恰是最可能刚粘贴了真实凭据的时刻。
- 功能被关闭时是**拒绝**而不是忽略。悄悄丢掉这个参数，会让请求从实例自己的地址发出去，而你以为它走了你的代理。

代价是真实存在的，不是白送的选项：这个身份的 cookie 是在某个地址后面签发的，现在却从另一个地址出示，这正是平台看得见的那种自相矛盾。两个通过不同代理请求同一条作品的调用方，问的不是同一个问题，所以永远不会被合并。

## `?explain=`

把请求实际发出去的样子还给你——签好名的 URL、请求头，以及该身份的 Cookie 罐——这样它可以在本实例之外被重放。它是「平台拒绝了我们，我需要看看我们到底发了什么」的排查工具。

- 需要 `identity:manage` **以及** `operator` 角色，因为答案里含有凭据。一个 `douyin:read` 的 key 可以要求本实例**使用**某个 jar；它不能要求把 jar 交到自己手上。
- 每次使用都会写进审计日志——谁问的、问的哪个接口。这行日志不含任何 jar。
- 它**隐含 `refresh`**：对一个缓存答案做「解释」，描述的会是这次请求根本没发生过的调用。它同时关闭任务合并，所以一个带解释的调用永远不会被合并到已经在途的、不带解释的调用上。
- 该块出现在结果元数据的 `explain` 下，含 `method`、`url`、`headers`、`cookie_header`、`identity_id`、`signer`、`endpoint` 和 `proxy`。
- **失败**的任务上同样会记录——那正是它存在的场景。
- 对任何没有 `identity:manage` 的读者，它会从存下来的任务结果里被剥掉；而且它永远不会出现在 SSE 流上。

参见[调试台与工具](./07-playground-and-tools.md)，那是同一件事的控制台前端。

## 接口分组

`{platform}` 是 `douyin` 或 `tiktok`，且必须与你传的链接一致；不一致会得到 `INVALID_URL`，`details.reason` 为 `"platform_mismatch"`。

### 内容 —— 异步，消耗身份

| 方法 | 路径 | 权限范围 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/parse` | 任一读取权限范围 | 任何受支持的链接，或包着链接的整段分享文案 |
| `POST` | `/api/v1/tasks/batch` | 任一读取权限范围 | 最多 50 条链接，每条一个任务 |
| `GET` | `/api/v1/{platform}/video` | `{platform}:read` | 单条作品。`url` **或** `aweme_id` |
| `GET` | `/api/v1/{platform}/video/comments` | `{platform}:read` | 分页 |
| `GET` | `/api/v1/{platform}/video/comments/replies` | `{platform}:read` | `comment_id` 加上 `url` **或** `aweme_id`。分页 |
| `GET` | `/api/v1/{platform}/user` | `{platform}:read` | 作者资料。`url` **或** `sec_user_id` |
| `GET` | `/api/v1/{platform}/user/posts` | `{platform}:read` | 分页 |
| `GET` | `/api/v1/{platform}/user/likes` | `{platform}:read` | 分页。见下面的平台差异说明 |
| `GET` | `/api/v1/{platform}/mix/posts` | `{platform}:read` | `mix_id`——抖音叫 `mix_info`，TikTok 叫 `playlistId`。分页 |
| `GET` | `/api/v1/{platform}/user/followers` | `tiktok:read` | **仅 TikTok** |
| `GET` | `/api/v1/{platform}/user/following` | `tiktok:read` | **仅 TikTok** |

除了核心那五个之外，两个平台并不对称，而这种不对称是被如实报告出来、而不是被抹平的。向抖音要 `followers` 或 `following` 会返回 `UNSUPPORTED_CONTENT`，`details.supported` 列出哪些平台提供它——如果路由照样把任务提交上去，你会拿到一个空页，然后得出「这个作者没有粉丝」的结论。`user/likes` 两个平台都提供，但抖音不会把这个列表给游客身份看：它需要一份导入的已登录身份；而在 TikTok 上，空页通常意味着作者把喜欢列表设为私密。

作品用 `url` **或** `aweme_id` 指定，不能都给也不能都不给；作者用 `url` **或** `sec_user_id`（TikTok 里叫 `secUid`）。已经带 ID 的 URL 会在入口处把 ID 提取出来，所以两种写法会合并到同一个任务上。夹着链接的文本也能接受——平台 App 生成的剪贴板内容可以原样发过来。短链接（`v.douyin.com`、`vm.tiktok.com`）会被排队交给 worker 展开，因为跟随一次跳转是一次网络调用，而 API 容器不做网络调用。

调用方自己输入的 ID 会在花掉任何代价之前被校验：`aweme_id=not-an-id` 和 `aweme_id=7123` 都会得到带 `details.field` 的 `INVALID_PARAM`，由我们免费答复，而不是发到上游去换回同一句话。

### 其余部分

| 前缀 | 是什么 | 由哪篇文档覆盖 |
| --- | --- | --- |
| `/api/v1/tasks/…` | 轮询、订阅、取消任务 | 本文 |
| `/api/v1/tools/…` | `parse-url`、`parse-batch`、`sign`、`decode`、`identity`——试跑与签名，前四个不消耗身份 | [调试台与工具](./07-playground-and-tools.md) |
| `/api/v1/archive/…` | 本实例已存下来的内容：检索、统计、合集、导出、复检、回填 | [下载、素材库与监控](./08-downloads-and-library.md) |
| `/api/v1/downloads/…` | 存在运营方磁盘上的媒体 | [下载、素材库与监控](./08-downloads-and-library.md) |
| `/api/v1/admin/…` | 身份、代理、密钥、用户、设置、日志、关注列表、备份 | [运维](./10-operations.md)、[用户与 API 密钥](./09-users-and-api-keys.md) |
| `/api/v1/auth/…` | 控制台会话与改密码 | [用户与 API 密钥](./09-users-and-api-keys.md) |
| `/api/v1/ios/…` | iOS 快捷指令的发布信息——无需认证 | 本文 |
| `/api/v1/system/status` | 版本、健康、身份池统计、存储 | [运维](./10-operations.md) |

归档类接口全部从本地存储作答：不消耗身份、不发起抓取，而一条后来被删掉的作品仍然在那里，`availability` 会说明这件事。所以想要「实例已经知道的东西」就用 `GET /api/v1/archive`，想要「最新的」才用 `/parse`。

## 不走信封的响应

有五个接口刻意用别的形式作答，分成四种形态。别把它们喂给你的信封解析器。

| 接口 | Content type | 为什么 |
| --- | --- | --- |
| `GET /healthz`、`GET /readyz` | 普通 JSON 对象 | 给负载均衡器用的探针；`/healthz` 不碰任何依赖，`/readyz` 在 Postgres 或 Redis 挂掉时返回 `503` |
| `GET /api/v1/tasks/{task_id}/events` | `text/event-stream` | Server-sent events |
| `GET /api/v1/archive/export` | `application/x-ndjson` | 每行一条作品，按页流式输出；上限 50000 行；需要 `archive:export` |
| `GET /api/v1/downloads/{download_id}/files/{name}` | 文件自身的类型 | 已存的文件字节，带一个让浏览器保存而不是渲染的 `Content-Disposition` |

## 完整示例

提交一个链接、轮询任务、读出结果。三个客户端做的是同一件事。

### curl

轮询版本，用 `python3` 从 JSON 里取一个字段：

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE="${DTK_BASE_URL:-http://127.0.0.1:8000}"
KEY="$DTK_API_KEY"
LINK="https://www.douyin.com/video/7123456789012345678"

field() { python3 -c 'import json, sys
value = json.load(sys.stdin)
for key in sys.argv[1:]:
    value = value[key]
print(value)' "$@"; }

task=$(curl -sS -X POST "$BASE/api/v1/parse" \
  -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"url\": \"$LINK\"}" | field data task_id)

echo "task $task"

for _ in $(seq 1 60); do
  body=$(curl -sS "$BASE/api/v1/tasks/$task" -H "X-API-Key: $KEY")
  state=$(printf '%s' "$body" | field data state)
  case "$state" in
    done)   printf '%s' "$body" | python3 -m json.tool; exit 0 ;;
    failed) printf '%s' "$body" | python3 -m json.tool; exit 1 ;;
  esac
  sleep 2
done

echo "still running after two minutes; task $task is still valid" >&2
exit 1
```

同步的一行版，适合你只是想在终端里看到答案：

```bash
curl -sS -X POST "$DTK_BASE_URL/api/v1/parse?wait=20" \
  -H "X-API-Key: $DTK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.douyin.com/video/7123456789012345678"}' \
  | python3 -m json.tool
```

记住 `?wait=20` 仍然可能返回 `202`——那是任务还在跑，不是失败。

### Python（httpx）

```python
"""Submit a link, poll the task, print the result. Needs: pip install httpx"""

from __future__ import annotations

import os
import time

import httpx

BASE = os.environ.get("DTK_BASE_URL", "http://127.0.0.1:8000")
HEADERS = {"X-API-Key": os.environ["DTK_API_KEY"]}

RETRYABLE = {
    "RATE_LIMITED",
    "QUEUE_FULL",
    "IDENTITY_POOL_EXHAUSTED",
    "ENDPOINT_CIRCUIT_OPEN",
    "UPSTREAM_RISK_CONTROL",
    "SIGNING_FAILED",
    "DOWNLOADER_UNAVAILABLE",
    "INTERNAL",
}


class DtkError(RuntimeError):
    def __init__(self, error: dict, request_id: str | None = None) -> None:
        super().__init__(f"{error['code']}: {error.get('message', '')}")
        self.code = error["code"]
        self.retry_after = error.get("retry_after")
        self.details = error.get("details")
        self.request_id = request_id


def unwrap(response: httpx.Response) -> dict:
    """Return `data`, or raise the envelope's error."""
    body = response.json()
    if not body.get("success"):
        raise DtkError(body["error"], response.headers.get("X-Request-ID"))
    return body["data"]


def submit(client: httpx.Client, url: str) -> str:
    """Queue a parse and return the task id, retrying while the instance pushes back."""
    for attempt in range(5):
        try:
            return unwrap(
                client.post("/api/v1/parse", json={"url": url})
            )["task_id"]
        except DtkError as exc:
            if exc.code not in RETRYABLE or attempt == 4:
                raise
            time.sleep(exc.retry_after or 2 ** attempt)
    raise RuntimeError("unreachable")


def collect(client: httpx.Client, task_id: str, deadline: float = 120.0) -> dict:
    """Poll until the task settles. A failed task is HTTP 200 with state 'failed'."""
    delay, end = 0.5, time.monotonic() + deadline
    while time.monotonic() < end:
        task = unwrap(client.get(f"/api/v1/tasks/{task_id}"))
        if task["state"] == "done":
            # The result is nested: envelope.data.data
            return task["data"]
        if task["state"] == "failed":
            raise DtkError(task["error"])
        time.sleep(delay)
        delay = min(delay * 2, 5.0)
    raise TimeoutError(f"task {task_id} still running; it remains valid, poll it again")


def main() -> None:
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=30.0) as client:
        task_id = submit(client, "https://www.douyin.com/video/7123456789012345678")
        print("task", task_id)
        result = collect(client, task_id)
        print(result.get("title") or result.get("description"))


if __name__ == "__main__":
    main()
```

这段代码里有两处才是重点：`unwrap` 按 `success` 和 `error.code` 分支，绝不按 message；`collect` 把 `failed` 的任务当作数据而不是 HTTP 失败来处理，因为它本来就是数据。

### JavaScript（fetch）

```javascript
// Node 18+ or any modern browser (see "Calling from a browser" for CORS).
const BASE = process.env.DTK_BASE_URL ?? "http://127.0.0.1:8000";
const HEADERS = { "X-API-Key": process.env.DTK_API_KEY, "Content-Type": "application/json" };

const RETRYABLE = new Set([
  "RATE_LIMITED", "QUEUE_FULL", "IDENTITY_POOL_EXHAUSTED", "ENDPOINT_CIRCUIT_OPEN",
  "UPSTREAM_RISK_CONTROL", "SIGNING_FAILED", "DOWNLOADER_UNAVAILABLE", "INTERNAL",
]);

class DtkError extends Error {
  constructor(error, requestId) {
    super(`${error.code}: ${error.message ?? ""}`);
    this.code = error.code;
    this.retryAfter = error.retry_after ?? null;
    this.details = error.details ?? null;
    this.requestId = requestId ?? null;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function unwrap(response) {
  const body = await response.json();
  if (!body.success) throw new DtkError(body.error, response.headers.get("X-Request-ID"));
  return body.data;
}

async function submit(url) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const data = await unwrap(await fetch(`${BASE}/api/v1/parse`, {
        method: "POST",
        headers: HEADERS,
        body: JSON.stringify({ url }),
      }));
      return data.task_id;
    } catch (err) {
      if (!(err instanceof DtkError) || !RETRYABLE.has(err.code) || attempt === 4) throw err;
      await sleep(1000 * (err.retryAfter ?? 2 ** attempt));
    }
  }
}

async function collect(taskId, deadlineMs = 120_000) {
  let delay = 500;
  const end = Date.now() + deadlineMs;
  while (Date.now() < end) {
    const task = await unwrap(await fetch(`${BASE}/api/v1/tasks/${taskId}`, { headers: HEADERS }));
    if (task.state === "done") return task.data;   // envelope.data.data
    if (task.state === "failed") throw new DtkError(task.error);
    await sleep(delay);
    delay = Math.min(delay * 2, 5000);
  }
  throw new Error(`task ${taskId} still running; it remains valid, poll it again`);
}

const taskId = await submit("https://www.douyin.com/video/7123456789012345678");
console.log("task", taskId);
console.log(await collect(taskId));
```

## 从浏览器调用

跨域请求默认被拒绝：`security.cors_allow_origins` 是空的，意味着仅同源，而预检请求会发现根本没有 `OPTIONS` 路由来应答它。想要浏览器访问的运营方，需要在那个设置里逐条写明来源。

- 跨域允许的方法：`GET`、`POST`、`PUT`、`DELETE`、`OPTIONS`。
- 浏览器端能读到的响应头：`X-Request-ID`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`、`Retry-After`。不暴露这些的话，关联 ID 和限额余量对 `fetch()` 就是不可见的，而那正是它们存在的大半意义。
- 把来源列表设成 `*` 会强制关掉凭据。填 `*` 的运营方要的是一个开放的匿名 API，不是一个开放的已认证 API——何况浏览器本来也会拒绝这种组合。

不要把 API key 发到你控制不了的页面里：任何读到那个页面的人都持有了这个 key，可以花掉你的身份池。要么在前面放一个你自己的小服务，要么用 `api.public_endpoints` 精确开放你需要的那几个接口并接受匿名限额。

## 限制与默认值

下面每一项都来自代码；标注 *（设置项）* 的可以在控制台设置页运行时修改——参见[配置参考](./03-configuration.md)。

| 项目 | 值 |
| --- | --- |
| `?wait=` 上限 *（设置项 `api.max_wait_seconds`）* | 30 秒 |
| 默认速率限制 *（设置项 `api.default_rate_limit_per_min`）* | 120 次/分钟 |
| 队列上限 *（设置项 `sched.queue_max`）* | 500 个任务 |
| `QUEUE_FULL` 的重试提示 *（设置项 `sched.max_wait_seconds`）* | 10 秒 |
| 任务结果保留 *（设置项 `retention.task_result_hours`）* | 24 小时 |
| 缓存 TTL，单条作品 *（设置项 `cache.content_ttl`）* | 1800 秒 |
| 缓存 TTL，作者资料 *（设置项 `cache.author_ttl`）* | 900 秒 |
| 缓存 TTL，任何分页列表 *（设置项 `cache.list_ttl`）* | 300 秒 |
| 任务合并占位存活时间 | 90 秒 |
| 平台分页大小（`count`） | 默认 20，最大 50 |
| 归档分页大小（`limit`） | 默认 50，最大 200 |
| 下载 / 管理列表（`limit`） | 默认 100，最大 500 |
| 单次批量条数 | 50 |
| 请求体上限 | 1 MiB（1,048,576 字节） |
| `url` 参数长度 | 4096 字符 |
| `cursor` 长度 | 512 字符 |
| `aweme_id` / `mix_id` / `comment_id` 长度 | 64 字符 |
| `sec_user_id` 长度 | 256 字符 |
| `callback_url` 长度 | 2048 字符 |
| `proxy` 长度 | 512 字符 |
| SSE 流 `timeout` | 1–300 秒，默认 300 |
| SSE 心跳 | 约 15 秒 |
| Webhook 尝试次数 / 超时 / 退避 | 3 次 / 10 秒 / 2 秒后 8 秒 |
| 归档导出上限 | 50000 行 |

有一处文档上的小瑕疵需要知道：API 文档自己的首页（在 `/swagger` 可见）说 `callback_url` 的主机「必须在运营方的 `security.url_allowlist` 里」。那个名单管的是**短链接**展开时可以经过哪些主机，与回调无关。回调真正的开关是 `security.enable_task_webhook`，外加上文说的 https 与非私有地址检查。

## 下一步

- [快速开始](./01-quickstart.md)——还没有跑起来的实例时，Base URL 和第一个 key 都来自这里
- [核心概念](./04-concepts.md)——身份、调度器、熔断器，以及一次请求的代价来自哪里
- [配置参考](./03-configuration.md)——本文提到的每一个设置项
- [用户与 API 密钥](./09-users-and-api-keys.md)——创建并限定本文示例所用的那个 key
- [调试台与工具](./07-playground-and-tools.md)——`explain`、签名和链接识别的控制台前端
- [MCP 与 AI 客户端](./12-mcp.md)——同样的能力，作为 agent 工具
- [命令行参考](./13-cli.md)——同样的能力，在终端里
- [故障排查](./14-troubleshooting.md)——上面某个错误码反复出现时该怎么办
