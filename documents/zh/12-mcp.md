# MCP 与 AI 客户端

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** ——
> 自部署的抖音 / TikTok 数据接口服务：REST、MCP 和 Web 控制台，身份池自维护。
> [文档首页](../README.zh-CN.md) · [English](../en/12-mcp.md)

这一页讲怎么把 AI 客户端——Claude Code、Claude Desktop、Codex CLI、Cherry Studio，或者任何支持 MCP 的工具——接到你自己的实例上，八个工具各自做什么，以及返回的东西该怎么读。读完你应该能配好一个客户端、用 `curl` 验证连通性，并且认得出导致大部分连接失败的那两个错误。

## MCP 接入点是什么

MCP 服务器跑在 **API 进程内部**。它不是 REST API 的包装层：REST、MCP 和 CLI 是同一套服务层的三个入口，所以一次工具调用直接落到调度器、身份池和响应缓存上，不多一跳 HTTP，也不会把已经认证过的调用方再认证一遍。

由此带来的实际影响：

- 一次工具调用消耗身份池的方式，和等价的 REST 调用完全一样。它走同一套按 Key 计的限流，写进同一份请求日志，也受同一个熔断器管辖。
- 命中缓存的答案来自同一份缓存。如果你的代理去要一个两分钟前刚被控制台用户抓过的作品，就不会产生任何上游请求。
- 改动运行时设置（比如 `api.mcp_tool_timeout`）不需要重启就能对 MCP 服务器生效，因为工具是通过一个支持热加载的 callable 读配置的。

在围绕它设计代理之前，有两条刻意的限制值得先知道：

- **只有八个工具，不会更多。** 每多一个工具，代理挑对工具的准确率都会有可测量的下降，所以新能力应该是现有工具的一个参数，而不是第九个工具。如果你需要覆盖这个实例全部能力的接口，请用 [REST API](./11-api.md)。
- **没有任何工具能碰到凭证。** 这里没有身份工具、没有 Cookie 工具、没有代理工具，`pool_status` 返回的也只是计数和熔断状态，而不是数据行。这是结构性的保证——那些工具根本不存在——而不是某个人要记得写的权限检查。接到这个实例上的代理读不到、也导不出你的 Cookie，更不可能被话术骗着去做。

工具做的全部是读操作。下载、关注列表、设置、身份管理和用户管理属于控制台和 REST 的地盘，见[下载、资料库与关注列表](./08-downloads-and-library.md)和[用户与 API 密钥](./09-users-and-api-keys.md)。

## 接入地址

| 项目 | 值 |
| --- | --- |
| 传输方式 | streamable-http |
| 路径 | `/mcp/`——挂在 API 进程上，和 `/api/v1` 并列 |
| 默认 compose 绑定下的 URL | `http://127.0.0.1:8000/mcp/` |
| 会话模式 | 无状态：不需要保存 `Mcp-Session-Id`，调用前也不必先做 `initialize` 握手 |
| 认证 | API Key，放在 `Authorization: Bearer …` 或 `X-API-Key: …` 里 |
| 必需的 `Accept` | `application/json, text/event-stream` |
| 服务名 / 版本 | `dtk` / 已安装的包版本 |
| 工具 | 8 个，全部只读 |
| 工具文本语言 | 始终是英文 |

**末尾的斜杠要保留。** 接入点是 `/mcp/`；`/mcp` 会以 `307 Temporary Redirect` 跳到它。行为规范的客户端会在 307 之后重发 POST 请求体，根本察觉不到；但如果客户端跳转后改用 GET，或者干脆不跟随跳转，报出来的错看起来就像认证有问题。配置时带上斜杠，这个问题就永远不会出现。

这个接入点不在 OpenAPI 文档里，所以你在 `/swagger` 里找不到它。控制台有一个对应页面 `/mcp-guide`（导航里叫 **MCP**），它会用你打开该页面时的地址自动填好接入地址，并给出与本页相同的客户端配置片段。

**工具文本始终是英文。** 控制台和 REST 的错误信息会跟随 `api.default_language` 与调用方的 `Accept-Language`；MCP 不会，因为 MCP 客户端从不发送语言，而另一端读它的是模型。把 `api.default_language` 设成 `zh` 并不会改变工具的返回文本。

## 认证

这个接入点只认 API Key，别的一概不认。

- **请求头：** `Authorization: Bearer dtk_…` 或 `X-API-Key: dtk_…` 均可，两者都在解析 JSON-RPC 请求体之前就被检查。
- **控制台的会话 Cookie 会被拒绝。** Cookie 是浏览器自动带上的，Key 是程序主动出示的。如果这里接受 Cookie，那么任何能从已登录浏览器里发出请求的东西都能碰到 MCP 接入点。
- **Key 至少要有一个平台的读权限。** 传输层要求 `douyin:read`、`tiktok:read` 或 `admin`；一个只有 `archive:read` 的 Key 会在任何工具运行之前就被 `FORBIDDEN_SCOPE` 拒掉。
- **限流用的就是 REST 那一套限流器**，按 API Key 计数：`api.default_rate_limit_per_min`（默认 **120** 次/分钟），除非该 Key 自己带了限额。注意每一次 JSON-RPC 调用都是一个 HTTP 请求，所以 `initialize`、`tools/list` 和每一次 `tools/call` 都各算一次。
- **把接口开放给匿名调用者并不会顺带开放 MCP。** “接口访问控制”作用于 REST 路径，MCP 的守卫无论如何都要求一个真实的 Key。

失败使用与 REST 相同的信封结构，所以一套错误处理逻辑就能覆盖两个接口：

```json
{ "success": false, "error": { "code": "UNAUTHENTICATED", "message": "…" } }
```

401 还会带上 `WWW-Authenticate: Bearer realm="dtk"`。

创建 Key：控制台 → **访问控制 → API Key**，或者 `POST /api/v1/admin/api-keys`。只给它 `douyin:read`、`tiktok:read` 或两者，别给多余的。完整的 Key 只在创建时显示一次，之后无法找回；丢了就撤销重建。详见[用户与 API 密钥](./09-users-and-api-keys.md)。

## 工具

| 工具 | 作用 | 排入队列的任务 endpoint |
| --- | --- | --- |
| `parse_url` | 解析链接指向的任何内容 | `parse` |
| `get_video` | 按 ID 获取单个作品——视频或图集 | `{platform}.content_detail` |
| `get_user` | 获取单个作者资料 | `{platform}.author_profile` |
| `list_user_posts` | 分页列出某作者的作品 | `{platform}.author_posts` |
| `list_comments` | 分页列出某作品的一级评论 | `{platform}.comments` |
| `get_content_history` | 读取某作品或作者已记录的指标历史 | 无——读本地数据库 |
| `pool_status` | 身份池与接口健康度 | 无——读 Redis 和本地数据库 |
| `get_task_result` | 取回没能在限时内完成的结果 | 无 |

第三列的 endpoint 名字，就是调度器、熔断器、健康看板和请求日志用的同一套键，所以代理发起的调用很容易在[运维](./10-operations.md)里找到对应记录。

`platform` 取值为 `douyin` 或 `tiktok`。**所有 ID 都是字符串**，绝不是数字：抖音和 TikTok 的 ID 超出了 JavaScript 安全整数范围，把它们当数字解析的客户端会悄无声息地把值弄坏。

### `parse_url`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `url` | 是 | 一个抖音或 TikTok 链接，或者包含链接的分享文本。短链（`v.douyin.com`、`vm.tiktok.com`）由服务端跟随展开。 |

手上是链接而不是 ID 时就从这里开始：它自己判断平台和资源类型。夹带链接的分享文案也能接受——取字符串里的第一个 URL。

返回 `status`、`task_id`、`platform`、`resource`、`url`（归类后的链接）和 `data`。

以下情况会直接拒绝、不消耗任何配额：链接的域名不属于抖音或 TikTok（`INVALID_URL`）；链接在受支持的域名上但不指向作品或作者主页——信息流、搜索页、设置页（`UNSUPPORTED_CONTENT`）；以及指向直播间、合集、音乐页、话题或搜索结果的链接，这套工具没有对应的 endpoint（`UNSUPPORTED_CONTENT`）。

### `get_video`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `platform` | 是 | `douyin` 或 `tiktok` |
| `content_id` | 是 | 字符串形式的作品 ID：抖音是 `aweme_id`，TikTok 是 item id |

在 `data` 下返回归一化后的作品——作者、统计数据、媒体地址、标签。

### `get_user`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `platform` | 是 | `douyin` 或 `tiktok` |
| `uid` | 是 | 抖音：`sec_user_id`（以 `MS4wLjAB` 开头）。TikTok：`secUid`，实在只有 `@handle` 时也可以传。 |

结果里的 `uid` 才是这个作者的稳定标识。TikTok 的 `@handle` 是会变的，所以代理后续调用应该带上这里返回的 `uid`，而不是 handle。

只有 TikTok 的资料接口接受 handle。抖音上传 handle 会被拒绝，并附带一句提示：改用 `parse_url` 传一个主页链接，由它来解析——那是抖音 handle 转成 ID 的唯一路径。

### `list_user_posts`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `platform` | 是 | `douyin` 或 `tiktok` |
| `uid` | 是 | 作者的稳定 ID。这里**不接受** TikTok 的 `@handle`——先调 `get_user`。 |
| `cursor` | 否 | 上一页返回的不透明 `cursor`。第一页不传；原样回传，永远不要去解析它。 |
| `count` | 否 | 每页条数，**1–50**。不传就用平台自己的默认值。 |

在 `data` 下返回一页数据，带 `cursor` 和 `has_more`。`has_more` 为 false 时停止。

`count` 上限是 50，因为两个平台都会对更大的值悄悄截断，结果就是代理拿到的条目比它要的少，而且没有任何提示。把每页调大并不能更快抓完一个大号——老老实实用 cursor 翻页。

### `list_comments`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `platform` | 是 | `douyin` 或 `tiktok` |
| `content_id` | 是 | 要看评论的作品 ID |
| `cursor` | 否 | 同上 |
| `count` | 否 | 同上，1–50 |

只返回一级评论。楼中楼回复是另一项能力，这套工具不暴露——这正是“只有八个工具”所付出的代价之一。代理确实需要时，走 REST 的 `/api/v1/{platform}/video/comments/replies`。

### `get_content_history`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `platform` | 是 | `douyin` 或 `tiktok` |
| `content_id` | 是 | 作品 ID；如果要看粉丝数历史，则传作者 ID |
| `since` | 否 | ISO 8601 日期或时间戳，例如 `2026-01-15` 或 `2026-01-15T00:00:00Z`。默认最近 **30 天**。 |

用本地已存的快照回答“它是怎么涨起来的”。它**不发起任何上游请求**，不消耗身份配额，因此是这套工具里最便宜的一个——同时也是唯一一个对本实例从未抓取过的 ID 返回不了任何东西的工具。每次成功解析都会顺带写一条快照，并按 `snapshot.min_interval_seconds`（默认 **300 秒**）去重；想要一条规整的曲线而不是零散几个点，就用[关注列表](./08-downloads-and-library.md)。

返回：

| 字段 | 含义 |
| --- | --- |
| `since` | 实际使用的窗口起点，ISO 8601 格式 |
| `point_count` | 返回的数据点数量 |
| `truncated` | 达到 **500** 点上限时为 true；保留的是最近的点，所以只有当你确实需要曲线更早的部分时，才去放宽 `since` |
| `points` | 按时间从旧到新。每个点含 `ts`、`play_count`、`digg_count`、`comment_count`、`share_count`、`collect_count`、`follower_count` |

平台没有给出的指标是 `null`，绝不会写成 `0`。null 是数据缺失，0 是一次真实测量——别让代理把两者混在一起求平均。

### `pool_status`

无参数。当请求失败或超时，你需要判断是该等、该换个平台，还是该收手时，调它。

返回：

| 字段 | 含义 |
| --- | --- |
| `observed_at` | 快照的采集时刻 |
| `identities` | 按平台给出各状态的计数：`minting`、`active`、`cooling`、`degraded`、`retired` |
| `usable_identities` | 各平台 `active` 身份的总数 |
| `endpoints` | 每个已知 endpoint 一条（见下） |
| `summary` | 一句英文，概括身份池状态和已熔断的 endpoint |

每条 endpoint 记录包含 `endpoint`、`state`（`closed` 或 `tripped`）、`retry_after_seconds`、`reason`、`requests_recent`、`ok_recent`、`risk_control_recent` 和 `last_success_at`。三个 `_recent` 计数取自熔断器 **300 秒**的滑动窗口；`last_success_at` 在最近 **30 天**内查找——但这次查找读的是 `request_log`，所以实际上限是 `retention.request_log_days`（默认 **14** 天）——窗口内没有成功记录则为 `null`。

这个结果里不会出现任何凭证、身份 ID、Cookie、指纹或代理地址。

### `get_task_result`

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `task_id` | 是 | 某次返回 `status: "pending"` 的工具调用给出的 task id |

除此之外没有别的理由调它。其他每个工具都会自己等结果，所以一上来就调这个的代理，是在轮询一件根本没人排队的活。

## 读取返回结果

每个工具返回一个 JSON 对象。成功时：

```json
{
  "status": "ok",
  "task_id": "3f6c…",
  "platform": "douyin",
  "content_id": "7100000000000000000",
  "cached": false,
  "data": { }
}
```

- `data` 就是数据模型本身——和 REST 返回的归一化结构一致，并且已经从任务信封里拆出来了，代理不用比 schema 描述的层级再往下挖一层。
- `cached` 在任务记录了这一信息时出现，表示这个答案是否真的产生了一次上游请求。相隔一分钟的两次调用完全可能返回相同数据，第二次带 `cached: true`。默认缓存 TTL：作品 1800 秒、资料 900 秒、列表 300 秒，见[配置参考](./03-configuration.md)。
- `task_id` 的作用是让这次调用能和请求日志对上，也让 `pending` 的结果可以稍后取回。

分页工具会把 `cursor` 和 `has_more` 放在 `data` 里面。

## 调用没能按时完成时

REST 是异步优先的：提交、拿 task id、再取结果。这套模型对代理是错的——把 task id 丢回去让它自己轮询会烧掉它的上下文，很多代理干脆就放弃了——所以 **MCP 的每个工具都会阻塞着等自己的答案**。活儿仍然走同一条异步链路，只是等待发生在服务端。

等待时间的上限是 `api.mcp_tool_timeout`，默认 **60 秒**（`dtk config set api.mcp_tool_timeout 90`，或者在控制台的设置页改；这是运行时设置，改完无需重启）。只有触到这个上限，代理才会听说“任务”这回事：

```json
{
  "status": "pending",
  "task_id": "3f6c…",
  "message": "The request for douyin.author_posts is still running after 60 seconds, so this tool call returned before it finished. Nothing was lost: the work continues in the background as task 3f6c…. Call get_task_result with task_id=3f6c… in a little while to collect it. Identity pool - douyin: 2 active, 1 cooling; tiktok: 3 active. No endpoint is tripped."
}
```

由此有三件事要注意，如果你的代理还没在读服务端的说明，就把它们写进系统提示词：

1. **拿到 `pending` 之后不要重发原来的调用。** 那会把同一份活儿排进队列两次，身份池也就被花掉两份。用那个 id 去调 `get_task_result`。
2. **把客户端的 HTTP 超时设得高于 `api.mcp_tool_timeout`。** 30 秒就放弃的客户端，会把一个服务端本来能答上来的请求报成网络故障。
3. **结果不会永久保存。** 任务行比它的结果多活 `retention.task_result_hours`（默认 **24 小时**）。要在这个窗口内取回 pending 的结果；过期之后 `get_task_result` 会明说结果已超出保留期，解决办法是重新调用原来的工具，而不是继续等。

对一个仍在运行的任务调用 `get_task_result`，会再次返回 `status: "pending"`，附带任务状态和身份池概况——它从不阻塞。

服务器还带了一段简短的说明，每个客户端都会在模型第一次调用前展示给它。内容是：从哪里开始、ID 都是字符串、`pending` 意味着什么，以及这里没有任何关于 Cookie、代理或身份的工具。

## 代理会看到的错误

调用失败会以 MCP 工具错误（`isError: true`）的形式返回，正文是人话，不是堆栈。每条消息都按同样的顺序说同样三件事：发生了什么、系统当前是什么状态、重试有没有用——最后用括号给出错误码，方便提示词做匹配。

```
That post does not exist. This was the douyin.content_detail endpoint.
Retrying will not help; the request has to change. (error code NOT_FOUND)
```

如果这次失败要靠 endpoint 的健康度才能解释清楚，消息里还会带上健康信息：

```
… Endpoint douyin.author_posts is currently tripped; last success was 2 hours ago.
Reason: Risk control hit 60% of the last 25 requests, across 3 identities. It reopens
in about 240 seconds. This is retryable: back off for about 240 seconds, then try
again. (error code ENDPOINT_CIRCUIT_OPEN)
```

“能不能重试”这句话由 REST 层用的同一张表生成，所以两边不可能给出互相矛盾的说法。

| 错误码 | 对代理意味着什么 | 能重试吗 |
| --- | --- | --- |
| `INVALID_URL` | 不是受支持的抖音或 TikTok 链接 | 不能——改请求 |
| `UNSUPPORTED_CONTENT` | 能识别，但不是作品或作者主页 | 不能 |
| `INVALID_PARAM` | 平台名不对、ID 为空、`count` 越界、`since` 解析不了，或者在需要稳定 ID 的地方传了 handle | 不能 |
| `FORBIDDEN_SCOPE` | 这个 Key 没被授予该平台 | 不能 |
| `NOT_FOUND` | 作品或作者不存在 | 不能 |
| `CONTENT_PRIVATE` | 存在，但游客身份看不到 | 不能 |
| `UPSTREAM_CHANGED` | dtk 的解析器坏了，换个请求也绕不过去 | 不能——请提 issue |
| `ENDPOINT_CIRCUIT_OPEN` | 该 endpoint 的熔断器已跳闸 | 能，等消息里说的时长 |
| `IDENTITY_POOL_EXHAUSTED` | 当前没有可用身份 | 能——池子会自己补 |
| `UPSTREAM_RISK_CONTROL` | 平台对这次请求做了风控 | 能，退避后再试 |
| `RATE_LIMITED` | 触到了这个 Key 自己的限额 | 能，等消息里说的时长 |
| `TASK_NOT_FOUND` | task id 不存在，或结果已过保留期 | 看消息——过保留期的那种会直接说明等待没有用 |
| `INTERNAL` | 请求在 dtk 内部就失败了 | 能，但要去看日志 |

有五个错误码的解释离不开健康状态——熔断打开、身份池耗尽、上游风控、签名失败和内部错误——这几种消息里会额外带上该 endpoint 的熔断状态和上次成功时间。异常文本只写日志、绝不外发：它可能包含主机名、查询串或请求头，这些都不该进入模型的上下文。

## 作用于代理的权限范围

传输层只能确认这个 Key “能读点什么”，因为平台参数在 JSON-RPC 请求体里而不在请求头里。所以检查做了两次：一次在门口，一次在每个工具内部针对实际请求的那个平台。

| 权限范围 | 对 MCP 的作用 |
| --- | --- |
| `douyin:read` | 打开接入点；允许调用抖音相关工具 |
| `tiktok:read` | 打开接入点；允许调用 TikTok 相关工具 |
| `admin` | 全部允许，包括两个平台 |
| 只有其他权限范围 | 接入点直接以 `FORBIDDEN_SCOPE` 拒绝 |

有两条规则常常出人意料：

- **Key 受限于它创建时的权限范围，哪怕拥有者是管理员。** 自托管实例上几乎每个 Key 都属于 admin 用户；一个只有 `douyin:read` 的 Key，通过 MCP 照样读不了 TikTok，就跟它调 `GET /api/v1/tiktok/video` 会被拒一样。
- **task id 不是绕过检查的后门。** `get_task_result` 会重新检查它要取回的那个任务属于哪个平台，所以只授权了一个平台的 Key，无法靠着从别处看到的 id 去读另一个平台的数据。（由 `parse_url` 排队的任务，其 endpoint 名里没有平台；那种情况下的检查发生在链接被归类时，也就是入队之前。）

走 stdio 时没有传输层身份可以约束：代理自己拉起了这个进程，本来就拥有该进程的一切，包括数据库。这是把 stdio 留给本机使用的理由，而不是 HTTP 传输的缺口。

## 客户端配置

下面每个片段都用 `http://127.0.0.1:8000/mcp/`，也就是默认 compose 部署发布的地址。请把它换成客户端真正访问得到的地址（原因见下文**常见错误**），并把 `dtk_YOUR_API_KEY` 换成真实的 Key。

### Claude Code

执行一次即可，它会把这个服务写进 Claude Code 的配置。加上 `--scope project` 可以改为写进当前仓库。

```bash
claude mcp add --transport http dtk http://127.0.0.1:8000/mcp/ \
  --header "Authorization: Bearer dtk_YOUR_API_KEY"
```

### Claude Desktop

`claude_desktop_config.json` 只校验 stdio 服务，而 Claude Desktop 的远程连接器界面走 OAuth，本服务不支持。所以这里通过 `mcp-remote` 中转——它是一个 stdio 进程，把请求转发到 streamable-http 接入点，并且可以携带固定请求头。需要本机装有 Node。打开 **设置 → 开发者 → 编辑配置**，把下面这段合并进去，然后重启应用。

```json
{
  "mcpServers": {
    "dtk": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp/", "--header", "X-API-Key:${DTK_API_KEY}"],
      "env": { "DTK_API_KEY": "dtk_YOUR_API_KEY" }
    }
  }
}
```

Key 走 `env`、请求头的值里不含空格，都是刻意的：`mcp-remote` 的文档写明，Windows 上的 Claude Desktop（以及 Cursor 和 Codex CLI）不会正确转义 `args` 里的空格，会把 `"Authorization: Bearer …"` 弄坏。`X-API-Key:${DTK_API_KEY}` 里没有让这个 bug 生效的地方，而本服务同样接受这个请求头。

### Codex CLI

追加到 `~/.codex/config.toml`，并在启动 Codex 的终端里 `export DTK_API_KEY=dtk_YOUR_API_KEY`。Key 从环境变量读取，配置文件本身可以放心提交。

```toml
[mcp_servers.dtk]
url = "http://127.0.0.1:8000/mcp/"
bearer_token_env_var = "DTK_API_KEY"
```

### Cherry Studio

**设置 → MCP 服务器 → 添加服务器 → 从 JSON 导入**。Cherry Studio 把这种传输方式叫 `streamableHttp`。

```json
{
  "mcpServers": {
    "dtk": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer dtk_YOUR_API_KEY" }
    }
  }
}
```

### 其他支持 streamable-http 的客户端

任何支持 streamable-http 的客户端都能用。它需要发出的请求长这样：

```http
POST http://127.0.0.1:8000/mcp/
Authorization: Bearer dtk_YOUR_API_KEY
Content-Type: application/json
Accept: application/json, text/event-stream
```

`Accept` 里两种媒体类型都必须有。传输层是无状态的，所以请求之间没有 session id 需要携带。

### stdio：同一台机器上的进程

给自己拉起服务进程、通过管道走 JSON-RPC 的代理用。这里没有 HTTP 请求，因此没有 API Key，也不受权限范围限制——调用方就是进程的属主。

```bash
# Runs on the same machine as the database, and reads the same
# environment the API process does. No API key: there is no HTTP
# request to authenticate.
DTK_DATABASE_URL=... DTK_REDIS_URL=... DTK_SECRET_KEY=... \
  python -m dtk.mcp
```

配置来自环境变量，和 API 进程完全一样，所以两边不会对着不同的数据库。日志在任何输出之前就被重定向到 stderr，因为在 stdio 模式下 stdout *就是* JSON-RPC 通道，一行多余的日志就会让客户端断开。

它的前提是网络可达：在默认的 Docker 部署里，Postgres 和 Redis 位于内部网络、不发布任何端口，所以在宿主机上启动的 `python -m dtk.mcp` 根本连不上它们。stdio 适合源码部署，或者本来就能访问这两个存储的进程。如果你跑的是 compose，请用 HTTP 传输——工具和行为完全一致。

## 验证是否可用

在客户端将要运行的那台机器上：

```bash
curl -sS -X POST http://127.0.0.1:8000/mcp/ \
  -H "Authorization: Bearer dtk_YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

传输层是流式返回的，所以你拿到的是一个 server-sent-events 帧——一行 `event: message`，后面跟着装有 JSON-RPC 结果的 `data:`——而不是一个裸 JSON 响应体。`tools/list` 成功时会列出全部八个工具。由于服务是无状态的，`tools/list` 和 `tools/call` 都不需要先做 `initialize` 握手，所以这条命令可以直接当健康检查用。

失败时长这样：

- `401`，`"code": "UNAUTHENTICATED"`——没带 Key 请求头，或者 Key 写错了、被撤销了、过期了。
- `403`，`"code": "FORBIDDEN_SCOPE"`——Key 有效，但没有任何平台读权限。
- `307`——末尾的斜杠掉了。
- `200` 且返回控制台的 HTML——你连的根本不是这个 API，或者中间的反向代理没有把 `/mcp/` 透传过去。

一次 `tools/call` 只要碰到平台，就是一次真实的、消耗身份池的请求。想在不花身份的前提下把链路整体跑通，调 `pool_status`：

```bash
curl -sS -X POST http://127.0.0.1:8000/mcp/ \
  -H "Authorization: Bearer dtk_YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pool_status","arguments":{}}}'
```

## 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 客户端报连接或认证错误，服务端日志里什么都没有 | 少了末尾斜杠；客户端在 307 之后没有重发 POST | 配置成 `/mcp/`，带上斜杠 |
| 在服务器上能用，从笔记本上不行 | 客户端配置里写的是 `127.0.0.1`。默认 compose 只把 API 发布在回环地址上 | 给客户端一个它访问得到的地址，并且在此之前给实例挂上 TLS——见[安全](./15-security.md) |
| 明明控制台已登录，却报 `401 UNAUTHENTICATED` | 这里不接受会话 Cookie | 创建一个 API Key，用请求头传 |
| 连接时就报 `403 FORBIDDEN_SCOPE` | Key 没有 `douyin:read`、`tiktok:read` 或 `admin` | 重新签发一个带平台读权限的 Key |
| 只有部分调用报 `FORBIDDEN_SCOPE` | Key 只有一个平台的权限，而代理要的是另一个 | 补上第二个权限范围，或者约束代理的行为 |
| 30 秒左右客户端超时，服务端还在干活 | 客户端的 HTTP 超时低于 `api.mcp_tool_timeout` | 调高客户端超时，或调低 `api.mcp_tool_timeout` |
| 代理在同一个失败调用上死循环 | 它没理会“重试没有用”那句话 | 在系统提示词里要求它先读错误文本再决定是否重试 |
| 代理把返回 `pending` 的调用又跑了一遍 | 它把 `pending` 当成了失败 | 明确告诉它用 task id 去调 `get_task_result` |
| ID 回来变短了或不对 | 客户端把 ID 当数字解析了 | 全程把 ID 当字符串 |
| `get_content_history` 什么都没返回 | 本实例从未抓过这个 ID | 先抓一次，或加入[关注列表](./08-downloads-and-library.md) |

如果不是配置问题而是工具真的在失败，第一件事是看 `pool_status`，其余的见[故障排查](./14-troubleshooting.md)。

## 接下来读什么

- [REST API 指南](./11-api.md)——完整接口，包括八个工具刻意不覆盖的部分。
- [用户与 API 密钥](./09-users-and-api-keys.md)——代理所用 Key 的创建、授权、限流与撤销。
- [核心概念](./04-concepts.md)——`pool_status` 所报告的身份池、调度器和熔断器。
- [运维](./10-operations.md)——在请求日志里找到代理发起的调用，读懂接口健康看板。
- [安全](./15-security.md)——把实例暴露到 localhost 之外以前必须做的事。
