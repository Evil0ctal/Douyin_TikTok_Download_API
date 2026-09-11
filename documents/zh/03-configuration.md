# 配置参考

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** ——
> 自部署的抖音 / TikTok 数据接口服务：REST、MCP 和 Web 控制台，身份池自维护。
> [文档首页](../README.zh-CN.md) · [English](../en/03-configuration.md)

读完本文，你会知道哪些配置需要重启、哪些不需要，如何从控制台、命令行或 API 三个入口读取和修改不需要重启的那些，以及全部 54 个运行时配置项各自的作用。

## 两层配置

配置分成两层，这个划分不是形式上的。

| 层 | 存放位置 | 修改方式 | 生效时机 |
| --- | --- | --- | --- |
| Bootstrap（引导层） | 环境变量，通常写在仓库根目录的 `.env` | 改文件，然后重启容器 | 重启后 |
| Runtime（运行时层） | Postgres 中的 `settings` 表 | 控制台、`dtk config set` 或管理 API | 几秒内生效，无需重启 |

引导层无法并进数据库的根本原因是 `DTK_SECRET_KEY`：它是加密所有 cookie 与代理凭证的主密钥，绝不能存在它自己负责解密的东西里面。`DTK_DATABASE_URL` 和 `DTK_REDIS_URL` 属于同一类问题——它们在数据库可达之前就必须先有。凡是不需要「先于数据库存在」的配置，都被刻意放进了数据库，这样调参就不必重启实例。

运行时配置的注册表是 `src/dtk/core/config.py` 里的 `RUNTIME_SETTINGS`。不在这张表里的键无法写入：拼错的键名会在边界上直接报错，而不会变成一个没人读的配置项。

## 一个值到底来自哪里

读取时的顺序很短：**先看 `settings` 表里有没有这一行，没有就用代码内置默认值。** 读取路径根本不查环境变量。

环境变量只有一次机会。当第一个管理员账号被创建时——也就是每个实例只做一次的 `/setup` 流程——所有 `RUNTIME` 与 `SENSITIVE` 作用域、且存在同名环境变量的键，会被复制进 `settings` 表。变量名是 `DTK_` 加上键名大写、点换成下划线：

| 配置键 | 为它播种的环境变量 |
| --- | --- |
| `cache.content_ttl` | `DTK_CACHE_CONTENT_TTL` |
| `security.request_proxy` | `DTK_SECURITY_REQUEST_PROXY` |
| `media.max_bytes` | `DTK_MEDIA_MAX_BYTES` |

那一次播种之后，数据库就是权威。这是自建实例上最常见的意外：**你改了 `.env`、重启了，结果什么都没变**，因为这个键已经落库，而落库的值优先。正确做法是去它现在所在的地方改（下面三种方式任选），或者把这个键重置回继承值。

在你相信一份配置清单之前，还有两点值得知道：

- 控制台和管理 API 会为每个键标注 `source`——`database`、`environment` 或 `default`——正是为了让这件事看得见，而不是靠猜。
- `source: environment` 的含义是「没有数据库行，且进程环境里存在这个变量」。如果你是在播种之后才加的这个变量，那么真正生效的仍然是代码默认值。只有通过控制台、命令行或 API 写一次，它才真的生效。

## Bootstrap 配置

由 `src/dtk/core/config.py` 中的 `BootstrapSettings` 在进程启动时读取一次，全部以 `DTK_` 为前缀；进程还会读取工作目录下的 `.env` 文件，compose 则把仓库根目录的 `.env` 传给每个容器。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DTK_SECRET_KEY` | 无——必填，至少 32 个字符 | 凭证加密的主密钥。镜像不提供任何默认值，缺少它时每个角色都拒绝启动。更换它会让已存储的 cookie 与代理凭证无法解密。 |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | Postgres 连接串，必须使用 asyncpg 驱动。 |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | Redis 地址。队列、缓存、令牌桶与控制台会话都在这里。 |
| `DTK_BIND_HOST` | `127.0.0.1` | API 监听地址。容器内会覆盖为 `0.0.0.0`，真正限制暴露面的是发布出去的端口。 |
| `DTK_BIND_PORT` | `8000` | API 监听端口。 |
| `DTK_LOG_LEVEL` | `info` | 日志级别，透传给 uvicorn。 |
| `DTK_LOG_JSON` | `true` | 结构化 JSON 日志。想在终端看人类可读的日志就设为 `false`。 |
| `DTK_BROWSER_RPC_URL` | 空 | 无头浏览器服务的地址，例如 `http://browser-rpc:9000`。留空表示关闭自动铸造身份，身份池只能靠手工导入的 cookie——这是受支持的降级模式，不是错误。 |
| `DTK_BACKUP_DIR` | `backups` | 备份归档的写入与列出目录。API 与 worker 必须一致：worker 写归档，API 负责列出和下载。容器镜像是只读的，所以 compose 把它指向一个共享卷。 |
| `DTK_DOWNLOADER_URL` | 空 | Go 媒体下载器的地址，例如 `http://downloader:9100`。留空则完全关闭媒体下载，此时 `POST /api/v1/downloads` 返回 501 并给出可操作的提示，其他功能不受影响。 |
| `DTK_DOWNLOADER_TOKEN` | 空 | 该 sidecar 的可选共享密钥。它所在的内部网络没有别的容器，所以这属于纵深防御而非边界本身；留空表示 sidecar 不做任何校验。 |

以下由进程或容器 entrypoint 直接读取，不在上面那个类里：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DTK_CONSOLE_DIR` | 未设置 | 控制台构建产物所在目录。镜像会设置它；源码检出时会回退到自己的构建输出。 |
| `DTK_FORWARDED_ALLOW_IPS` | 未设置 | 设为你的反向代理地址后，entrypoint 会带上 `--proxy-headers --forwarded-allow-ips` 启动 uvicorn。不设置时 `X-Forwarded-For` 一律忽略——没有可信代理时这个头由调用方控制。填 `*` 不算声明：那会让来源地址变得可伪造，代码把通配符视为不可信。 |
| `DTK_WORKER_COMMAND` | 未设置 | 覆盖 worker 的启动命令行。只有当 worker 模块不在常用名字下时才需要。 |
| `DTK_COMMIT`、`DTK_GIT_COMMIT`、`GIT_COMMIT` | 未设置 | 系统状态接口上报的提交号，按此优先级读取。 |

还有四个是 compose 自己读的，而不是应用读的：`DTK_IMAGE_TAG`（镜像标签，默认 `dev`）、`DTK_REDIS_MAXMEMORY`（Redis 内存上限，默认 `320mb`），以及 `CLOAKBROWSER_REPO` 与 `CLOAKBROWSER_COMMIT`——后两个是浏览器镜像的构建参数，而不是运行时会被读取的配置，见[安装与部署](./02-installation.md)。`DTK_BIND_HOST` 与 `DTK_BIND_PORT` 在这里身兼两职——它们同时决定对外发布的地址。这六个变量的插值，compose 读的都是 `docker/.env` 或 shell，绝不是根目录的 `.env`——把 `CLOAKBROWSER_COMMIT` 或 `DTK_IMAGE_TAG` 写进根目录 `.env` 不会有任何效果，原因就在这里。要发布到所有网卡：

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

这个端口前面该放什么，见[安装与部署](./02-installation.md)；为什么默认只监听回环，见[安全](./15-security.md)。

## Sidecar 环境变量

两个可选容器有各自的环境变量，同样在启动时读取一次。它们都不连数据库，compose 也把两者的 `DTK_SECRET_KEY` 置空。

### browser-rpc（`DTK_BROWSER_*`）

来自 `docker/browser_rpc/settings.py`。这些都写在根目录 `.env` 里；compose 只固定了由容器自身拓扑决定的那三个。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DTK_BROWSER_BACKEND` | `cloak` | 驱动哪个浏览器后端。后端之间没有回退：不设置就等于 `cloak`，绝不是「谁能起来用谁」。 |
| `DTK_BROWSER_BACKEND_PIN` | 未设置 | 由镜像烙进去的构建来源标识，在 `/rpc/health` 上报。 |
| `DTK_BROWSER_BIND_HOST` | `127.0.0.1` | 监听地址。容器内 compose 固定为 `0.0.0.0`。 |
| `DTK_BROWSER_BIND_PORT` | `9000` | 监听端口。compose 固定为 `9000`。 |
| `DTK_BROWSER_PROFILE_ROOT` | `/tmp/dtk-browser-profiles` | 浏览器 profile 的父目录。compose 固定为 `/profiles`，那是一块 tmpfs，因此一次性的铸造 profile 永远不落盘。 |
| `DTK_BROWSER_WARM_CONTEXTS` | `1` | 每个平台常驻的热签名上下文数量。每个常驻上下文大约占容器 `/tmp` 的 300 MB，再乘以两个平台——这是内存预算，不是磁盘空间。 |
| `DTK_BROWSER_WARM_REFRESH_SECONDS` | `1800` | 热上下文最多存活多久就重建。平台会不断更新自己的 JavaScript，页面放旧了就会用旧代码签名。 |
| `DTK_BROWSER_PREWARM` | `true` | 启动时预热，让第一次签名不必承担冷启动。 |
| `DTK_BROWSER_MAX_CONCURRENT_MINTS` | `2` | 最多同时铸造几个身份。 |
| `DTK_BROWSER_MINT_TIMEOUT_SECONDS` | `75` | 单次铸造的服务端预算。 |
| `DTK_BROWSER_SIGN_TIMEOUT_SECONDS` | `8` | 单次签名的服务端预算。 |
| `DTK_BROWSER_CONTEXT_OPEN_TIMEOUT_SECONDS` | `45` | 打开一个浏览器上下文允许耗时多久。 |
| `DTK_BROWSER_SDK_READY_TIMEOUT_SECONDS` | `25` | 新打开的页面在导航结束后，最多多久必须具备签名能力。页面可导航的时刻远早于其安全脚本加载完成，在这个窗口里签名会失败，而失败的样子非常像平台换了算法。 |
| `DTK_BROWSER_SIGN_PROXY_URL` | 未设置 | 热签名页面的可选出口。不设置时它们会用容器自身的地址访问平台站点。 |
| `DTK_BROWSER_GEO_PROBE_URL` | `https://ipinfo.io/json` | **经由身份自己的代理**访问，用来确认出口实际在哪。留空则关闭探测，只剩调用方给的地理提示作为时区与语言的来源。 |
| `DTK_BROWSER_GEO_PROBE_TIMEOUT_SECONDS` | `8` | 该探测的超时预算。 |
| `DTK_BROWSER_DEFAULT_COUNTRY` | `US` | 调用方和探测都没说出口在哪时使用的两位国家码。 |
| `DTK_BROWSER_HEADLESS` | `true` | 只有在有显示器的机器上调试时才关闭。 |
| `DTK_BROWSER_LOG_LEVEL` | `info` | sidecar 的日志级别。 |

### downloader（`DTK_DOWNLOADER_*`）

来自 `docker/downloader/main.go`。值为空、无法解析或不为正数时，一律回退到默认值。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DTK_DOWNLOADER_BIND` | `0.0.0.0:9100` | 监听地址。compose 固定它以匹配 `expose` 与健康检查。 |
| `DTK_DOWNLOADER_ROOT` | `/var/lib/dtk/media` | 唯一可写路径。compose 固定为媒体卷。 |
| `DTK_DOWNLOADER_TOKEN` | 空 | sidecar 期望的共享密钥；需与应用侧的 `DTK_DOWNLOADER_TOKEN` 配对。 |
| `DTK_DOWNLOADER_WORKERS` | `4` | 并发下载任务数。 |
| `DTK_DOWNLOADER_ITEM_WORKERS` | `4` | 单个任务内的并发文件数。 |
| `DTK_DOWNLOADER_QUEUE` | `64` | 内存任务队列深度。 |
| `DTK_DOWNLOADER_HISTORY` | `500` | 记住多少条已完成任务。 |
| `DTK_DOWNLOADER_MAX_REDIRECTS` | `5` | 单次传输的重定向上限。 |
| `DTK_DOWNLOADER_TIMEOUT_SECONDS` | `900` | 单次传输的超时上限。 |

## 修改一个运行时配置

三个入口，同一份注册表，同一条校验路径。无论用哪个，值都会被强制转换为声明的类型、对照允许的取值集合校验、跑一遍额外校验器，然后存储并广播。

### 在控制台里

**设置** 页按前缀分组展示所有键，每一行都带有键名、来源标签、默认值、对应的环境变量名，以及与类型匹配的编辑控件。`sched.*` 与 `pool.*` 刻意不在这里：它们在 **调度器** 页，紧挨着它们作用的那个身份池。`api.public_endpoints` 另有专门的 **接口访问控制** 页，`notify.*` 另有专门的 **通知** 页——同样的行，只是放在能讲清楚它的位置编辑。

来源为 `database` 的行提供 **重置为继承值**，也就是删除这条覆盖。viewer 角色看到的是只读页面。列表每 10 秒轮询一次，未编辑的行跟随服务端，正在输入的行保留你输入的内容。

### 用 `dtk config`

命令行是救援通道：控制台登不进去时它仍然可用。它需要与服务相同的环境，因此对 compose 部署来说，最简单的方式是在 API 容器里运行。

```bash
# 列出全部配置项，含当前值、默认值、作用域与说明
docker compose -p dtk -f docker/compose.yml exec api dtk config list

# 只看与内置默认值不同的项
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed

# 只看会扩大攻击面的那些键
docker compose -p dtk -f docker/compose.yml exec api dtk config list --scope sensitive

# 以 JSON 打印单个值
docker compose -p dtk -f docker/compose.yml exec api dtk config get cache.content_ttl

# 修改一项；列表用逗号分隔
docker compose -p dtk -f docker/compose.yml exec api dtk config set cache.content_ttl 3600
```

如果是本地检出且环境变量已导出，去掉 compose 前缀即可：`uv run dtk config list`。

`--scope` 接受 `bootstrap`、`env_only`、`runtime` 和 `sensitive`；实际能匹配到行的只有后两个，因为注册表里的每个键非此即彼。对敏感项执行 `dtk config set` 会先要求确认；`--yes` 跳过确认，这在自动化部署脚本里有用，在别处都不该用。

可能携带凭证的值在输出时一律掩码——配置转储是最容易被贴进 issue 的东西。

### 通过管理 API

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/v1/admin/settings` | 返回全部配置项，含 `value`、`default`、`scope`、`type`、`choices`、`description`、`sensitive`、`source`、`env_var`、`masked`、`updated_at`、`updated_by`，以及当前配置 `version`。 |
| `PUT /api/v1/admin/settings/{key}` | 请求体 `{"value": ..., "confirm": false}`。敏感键必须带 `confirm`。 |
| `DELETE /api/v1/admin/settings/{key}?confirm=` | 删除覆盖，使该键回退到继承值。敏感键必须带 `confirm=true`。 |

访问这些接口需要控制台会话，或一个具备 `admin` 或 `identity:manage` 权限范围的 API Key。修改非敏感键还需要 `operator` 及以上角色——viewer 会被拒绝。修改敏感键则需要同时具备 `admin` 范围、`admin` 角色，以及 `confirm`。

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/admin/settings/cache.content_ttl \
  -H "Authorization: Bearer $DTK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"value": 3600, "confirm": false}'
```

每次写入都会留下一条审计记录（敏感键是 `settings.updated_sensitive`，其余是 `settings.updated`），记录操作账号、修改前后的值，两者都已掩码。认证方式与响应信封见 [REST API 指南](./11-api.md)，角色与权限范围见[用户与 API 密钥](./09-users-and-api-keys.md)。

## SENSITIVE 意味着什么

有七个键被声明为 `SENSITIVE` 而不是 `RUNTIME`。这个作用域不是样式差异——它的含义是*放宽它就会扩大攻击面*，因此谁能改、改一次要付出什么代价，都随之改变。

| 键 | 放宽它会暴露什么 |
| --- | --- |
| `security.url_allowlist` | 短链展开时的 SSRF 边界。 |
| `security.cors_allow_origins` | 哪些站点的 JavaScript 可以带着用户的控制台 Cookie 调用本接口。 |
| `security.cors_allow_credentials` | 那些跨域请求是否允许携带 Cookie。 |
| `security.enable_task_webhook` | 本服务器是否会去访问调用方提供的 `callback_url`。 |
| `security.webhook_secret` | 让这些回调可被验证的那个密钥。 |
| `security.request_proxy` | 持有 API Key 的人能否指定本服务器的出口。 |
| `api.public_endpoints` | 每一条都从某个接口上移除了凭证校验。 |

其中六个属于 `security.*`；第七个 `api.public_endpoints` 虽然在 `api` 分组里，却是同样的作用域，因为它的每一条都取消了一次凭证校验。

一次敏感写入需要同时满足：

- 凭证具备 `admin` 范围，仅有 `identity:manage` 不够——自建环境里几乎每把 Key 都属于管理员，只靠角色根本不成其为边界；
- 账号是 `admin` 角色；
- 一次明确的确认——API 上是 `confirm: true`，控制台里要手动输入键名，命令行里要回答提示；
- 并且写入一条独立的审计动作。

敏感的*字符串*值在任何展示处都会以掩码显示。把掩码原样提交回来表示「保留已存储的值」，且仅此含义：提交的掩码必须正是所存值的掩码，因此调用方无法伪造一个掩码把凭证套出来。同样的规则逐字段应用于 `notify.channels` 内部，并且目标主机或 URL 发生变化的记录不会继承任何旧凭证。

## 类型，以及值该怎么写

| 声明类型 | 控制台控件 | `dtk config set` | API 请求体 |
| --- | --- | --- | --- |
| `bool` | 开关 | `true` / `false`（也接受 `1`、`yes`、`on`） | JSON `true` / `false` |
| `int`、`float` | 数字输入框 | 直接写数字 | JSON 数字 |
| 带 `choices` 的 `str` | 下拉选择 | 其中之一，不区分大小写 | JSON 字符串 |
| `str` | 文本框 | 直接写字符串 | JSON 字符串 |
| `list` | 多行文本框，每行一条 | 逗号分隔 | JSON 数组 |

错误的值在写入时就会被拒绝，并给出原因；对于有取值集合的键，还会附上合法取值列表。手工改进数据库、已经不合法的值不会致命：它会被记录日志并改用代码默认值，对白名单类配置来说这是「失败即收紧」的正确方向。

有六个键在类型之外还带有额外校验，归为四条规则：

| 键 | 规则 | 为什么要有这条规则 |
| --- | --- | --- |
| `capacity.warn_percent`、`capacity.hard_stop_percent` | 整数，1–99 | `0` 会让实例一启动就暂停；`150` 会让护栏永远不触发——而且是静默地不触发，这正是它要防的失败。 |
| `media.max_bytes`、`media.max_file_bytes` | `0`，或至少 1 MiB | 对 `media.max_bytes` 来说，`0` 表示「不设上限」，是操作者可以做的真实选择；对 `media.max_file_bytes` 来说，校验器接受 `0`，但下载器不接受（见下文）。两者都会拒绝 `1000`：它会拒绝所有传输，而且是以「单个文件报错」的形式，而不是「你的配置写错了」。 |
| `watchlist.min_interval_seconds` | 至少 60 | 低于一分钟，定时就不再是定时而是死循环——上一轮还没跑完就被重新入队，整个身份池会耗在一个目标上。 |
| `security.url_allowlist` | 裸主机名，且必须是公网可路由的；结果会转小写、去重、排序 | 粘贴了 URL 而不是主机名，或者写了指向本机 / 内网的名字，都会被**指名拒绝**，而不是存下来然后每次读取时被静默忽略。 |

## 运行时配置完整参考

全部 54 个键，按前缀分组。默认值与代码声明完全一致。

### `sched` — 调度（7 项）

在控制台的 **调度器** 页编辑。调度器与熔断器的作用见[核心概念](./04-concepts.md)。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `sched.max_wait_seconds` | `10` | 一个任务最多等待多久才能拿到空闲身份，超时后以 `IDENTITY_POOL_EXHAUSTED`（HTTP 503，带 `retry_after`）失败。租约是在 worker 里获取的，因此这个计时开始之前，提交早就已经返回了 task id。队列满时返回的 `Retry-After` 也用这个值。 | 身份池小、又常有短时峰值时调大——等待好过失败。想更早放弃、把 worker 让给下一个任务就调小。 |
| `sched.queue_max` | `500` | 允许排队的任务上限。超过后接口直接拒绝并返回 `Retry-After`，而不是收下没人来得及处理的活。 | 身份池小的时候调小它——队列再深也只是让人等更久。设为 `0` 关闭该检查。 |
| `sched.cooldown_base_seconds` | `60` | 身份第一次被风控后的冷却时长；连续命中会逐次翻倍。 | 平台对你的地址下手更重时调大。 |
| `sched.cooldown_max_seconds` | `21600` | 冷却时长上限。退避到达上限的身份会被标记为 `degraded`，而不是继续冷却。 | 想更快回收身份就调小，代价是去戳一个还在生气的平台。 |
| `sched.circuit_risk_threshold` | `0.6` | 触发接口熔断的风控率阈值，取值 0 到 1。 | 想更早放弃一个持续失败的接口就调低。 |
| `sched.circuit_min_samples` | `20` | 熔断前必须观察到的最少请求数，避免几次偶发失败就把接口熔断。 | 流量很大、20 次采样等于噪声时调大。 |
| `sched.circuit_min_identities` | `3` | 熔断前必须有多少个不同身份同时失败。用于区分「接口坏了」和「某一个身份坏了」。 | 只在排查问题时才调到 `1`；那等于抹掉了这个配置存在的意义。 |

### `pool` — 身份池（4 项）

同样在 **调度器** 页。参见[身份与代理](./06-identities-and-proxies.md)。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `pool.min_size` | `3` | 每个平台的身份数下限。低于此值会告警并触发铸造。 | 突发流量经常把池子打空时调大；不要超过 `target_size`。 |
| `pool.target_size` | `8` | 身份池为每个平台维持的目标数量。 | 想要更高吞吐就调大，代价是更多铸造与更多代理。小于 `min_size` 的值会被忽略。 |
| `pool.max_fail_streak` | `3` | 连续失败达到该次数后，这个身份不再计入池子水位，补充任务会去铸造替代品，而不是把一个什么都跑不通的会话继续算作有效。这里用连续失败次数而不是健康分数，是因为没有流量时健康分数为空——把刚铸出来的身份判为不可用，会让低水位变成不停铸造的死循环。 | 很少需要改。某个不稳定的代理导致反复重铸时可以调大。 |
| `pool.health_prior` | `0.8` | 尚无历史记录的身份所假定的成功率，取值 0 到 1。它决定新身份被调度的积极程度。 | 新身份还没证明自己就被风控时调低。 |

### `signing` — 请求签名（3 项）

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `signing.mode` | `'native'` | 由谁生成签名。`native` 运行内置纯算法，完全不碰浏览器；`rpc` 每次都请求 browser-rpc；`auto` 以 native 起步，遇到平台自身 SDK 会签名的接口、或某接口的风控率显示 native 签名正在被拒时，切到浏览器。 | 平台上线新 SDK 而内置算法还没跟上时切到 `rpc`。没有 browser-rpc 容器时，唯一自洽的模式是 `native`。 |
| `signing.fallback_enabled` | `True` | 当前模式不偏好的那个签名器，在首选不可用时是否仍可使用。 | 关掉它，才能知道当前到底是谁在扛流量——开着回退时，坏掉的签名器会因为流量悄悄转移而看起来一切正常。在 `auto` 下，风控驱动的切换本身就是回退，所以关掉它等于把 `auto` 钉死在 native。 |
| `signing.rpc_timeout_seconds` | `60` | 单次 browser-rpc 签名调用的超时上限。它必须覆盖冷启动：某个身份的第一次调用会启动浏览器，并经由该身份的代理加载平台页面——无代理实测约 4 秒——而同一身份的后续调用由常驻页面在毫秒级返回。 | 经由慢代理时容易超时就调大；如果问题出在冷启动，应该改为调大 `DTK_BROWSER_WARM_CONTEXTS`。 |

### `cache` — 响应缓存（3 项）

缓存放在 Redis 里，按不同请求分别成键。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `cache.content_ttl` | `1800` | 解析后的视频详情可复用多久，超时后重新抓取。 | 需要新鲜的计数就调小；想少花身份池配额在重复查询上就调大。单条缓存实测 16–200 KB，TTL 越长越吃 Redis 内存。 |
| `cache.author_ttl` | `900` | 解析后的用户主页信息可复用多久。 | 同样的取舍；主页比作品变化慢。 |
| `cache.list_ttl` | `300` | 解析后的列表页（作者作品、评论、评论回复）可复用多久。 | 列表比单条内容变化更快，所以通常是三者中最短的。 |

### `snapshot` — 内容历史（1 项）

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `snapshot.min_interval_seconds` | `300` | `content_snapshots` 的去重窗口。同一条内容在窗口内的第二次快照会被跳过而不入库。 | 要按分钟采样某条爆款时调小；历史表增长速度超过你实际会问的问题时调大。 |

### `archive` — 内容归档（5 项）

参见[下载、资料库与关注列表](./08-downloads-and-library.md)。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `archive.enabled` | `True` | 在任务结果过期后，继续把解析到的作品与作者保存在数据库中。 | 想让实例除日志之外无状态就关掉——那样今天解析过的作品明天就查不到了。 |
| `archive.store_raw` | `False` | 在归一化记录之外，同时保存平台的原始响应，便于日后补算新字段而无需重爬。 | 预计以后要重新提取字段时打开。它是本实例可选存储项里体积最大的一个：解析器会为每个对象都生成一份。 |
| `archive.recheck_after_days` | `7` | 一个作品距离上次存在性检查多久之后需要重新检查。正是它让「我存的哪些作品已经没了」这个问题有答案。设为 `0` 关闭重新检查。 | 在意作品是否被删就调小；想少花请求在这个问题上就调大。 |
| `archive.recheck_batch` | `25` | 一次检查扫描多少个作品。每个都是一次经过身份池的真实请求，所以这个值决定了这个答案的成本。 | 身份池大就调大。超过 200 的部分会被扫描逻辑本身截断。 |
| `archive.recheck_pause_seconds` | `3` | 重新检查或历史回溯的相邻请求之间间隔多少秒。调度器是按身份限速的，这对「按 IP 计数」的平台限制毫无作用。 | 这些扫描被限流时调大。没有人在等它们，慢一点不花什么代价。 |

### `retention` — 数据保留期（6 项）

两个超表（hypertable）的窗口由 TimescaleDB 策略执行，并由 worker 的维护轮次重新下发。`content_snapshots` 永远不会被加上保留策略：那是你自己积累的数据，删除它必须是一次手动且确认过的操作。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `retention.request_log_days` | `14` | 单条请求日志保留多少天。 | 需要在几天后复盘问题就调大；磁盘紧张就调小。取值必须在 1 到 3650 之间——超出范围时该策略会被跳过并记录错误。 |
| `retention.identity_events_days` | `90` | 身份状态变更记录保留多少天。要足够长，以便回答某个身份为什么被淘汰。 | 取值范围同上。 |
| `retention.task_days` | `90` | 已完成的任务记录保留多少天。 | 如果增长最快的是任务表就调小。 |
| `retention.task_result_hours` | `24` | 任务结果内容保留多少小时。比任务记录本身短，因为结果才是占空间的部分。 | 调用方在提交很久之后才来取结果时调大。 |
| `retention.retired_identity_days` | `90` | 身份退休后其记录保留多久。cookie 在退休时已被擦除，这里保留的只是该身份存在过的记录。 | 身份轮换很快的实例可以调小。 |
| `retention.content_days` | `0` | 本意是删除超过指定天数的归档作品。`0` 表示永不删除（默认）——归档的意义正是比平台活得更久。**当前没有任何代码读取这个键**，所以改它不会有任何效果；磁盘安全由容量护栏负责。 | 不用管它。 |

### `watchlist` — 定时采集（4 项）

调度器每分钟触发一次，每个条目自带各自的间隔。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `watchlist.enabled` | `True` | 是否运行定时采集。 | 想暂停提交新的采集任务、同时原样保留条目与已采集内容时关掉。 |
| `watchlist.min_interval_seconds` | `900` | 任何关注目标允许设置的最短间隔。 | 只有目标确实变化极快时才调小（下限 60）。每隔几秒采集一次并不会得到更好的时间序列，只会把整个身份池耗在一个作者身上。 |
| `watchlist.default_interval_seconds` | `21600` | 新增关注目标时，未指定间隔则使用这个值。六小时意味着一百个作者的关注列表每天约 400 次请求。 | 默认期望更细的粒度时调小。 |
| `watchlist.batch_size` | `10` | 单次调度最多提交多少个到期条目。剩下的仍然是到期状态，会在下一次调度提交，这样大的关注列表会被摊开到几分钟内，而不是一次性堵在正在使用接口的人前面。 | 关注列表大到永远追不上时调大；按默认值每小时最多 600 次采集。 |

### `media` — 媒体下载（4 项）

需要启用 `downloader` profile 并设置 `DTK_DOWNLOADER_URL`。两个上限都按实际写入的字节计算——`Content-Length` 和平台自称的 `size_bytes` 都只是声称，都不被信任。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `media.enabled` | `True` | 是否允许下载媒体。关闭后拒绝新任务，已保存的文件原样保留；这个开关不会删除任何东西。 | 想冻结存储增长又不丢已有文件时关掉。 |
| `media.max_bytes` | `2147483648`（2 GiB） | 已保存媒体可以占用的磁盘上限。超过后按最旧优先删除未置顶的下载，直到回到上限以下，并发出一条告警说明删了什么。置顶的下载永远不会被删除。`0` 完全关闭清理。 | 卷很大就调大。只有当你打算自己管理这个卷时才设 `0`。 |
| `media.max_file_bytes` | `536870912`（512 MiB） | 单个文件的大小上限。达到上限的传输会被拒绝，并且不留下任何残留文件。这个值大约是常见最大单个视频的两倍。校验器接受 `0`，但下载器会拒绝任何没有字节上限的条目（`item "…" has no byte ceiling`，以 400 返回），所以 `0` 不是「取消限制」，而是「关掉下载」。 | 要下长视频就把数字调大——绝不要设成 `0`。 |
| `media.mirror_max_age_seconds` | `600` | 归档里的媒体链接超过多久之后，下载前需要重新解析作品以获取新链接。带签名的 CDN 链接几小时内就会过期。 | `0` 表示每次都重新解析，代价是每次下载多消耗一次身份池请求。 |

### `capacity` — 磁盘护栏（2 项）

用 `shutil.disk_usage` 直接测量数据库与媒体所在路径，因此涵盖 WAL、索引、其他容器以及共用该卷的一切。这里的任何一项都不会删除数据。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `capacity.warn_percent` | `80` | 磁盘使用率达到该百分比时发出告警。 | 卷无法快速扩容时调低。 |
| `capacity.hard_stop_percent` | `92` | 磁盘使用率达到该百分比时，后台采集与新的下载任务暂停。交互式 API 读取永不暂停，也绝不删除任何数据——磁盘写满应当让实例降级，而不是让它下线或毁掉已采集的内容。 | Postgres 需要的余量超过整卷 8% 时调低。 |

### `api` — HTTP 接口面（5 项）

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `api.default_rate_limit_per_min` | `120` | 单个 API Key 每分钟允许的请求数，固定窗口。用途是防滥用，不是计费。单独设置了限额的 Key 以自己的为准；`0` 关闭该检查。 | 内部可信调用方可以调大——更好的做法是给那把 Key 单独设限额。开放接口上的匿名调用方按来源地址计数。 |
| `api.max_wait_seconds` | `30` | 调用方能传给 `?wait=` 的最大值。超过这个值会被**拒绝**而不是静默截断，这样一个想阻塞五分钟的调用方能立刻知道它做不到。 | 只有当你反向代理自身的超时更长时才调大。 |
| `api.mcp_tool_timeout` | `60` | 单次 MCP 工具调用最多阻塞多久，超时后改为返回 task id。下限 1 秒。 | 能容忍长调用的 agent 可以调大。参见 [MCP 与 AI 客户端](./12-mcp.md)。 |
| `api.default_language` | `'en'` | 请求未指定语言时使用的默认语言。支持 `en` 与 `zh`；单个请求始终可以用 `?lang=` 或 `Accept-Language` 覆盖。 | 中文团队设为 `zh`。不支持的值会回退到 `en`。 |
| `api.public_endpoints` | `[]`（**敏感项**） | 无需 API Key 即可访问的接口列表，每项写成 `<METHOD> <路径>`，与 API 文档中的写法完全一致，例如 `GET /api/v1/{platform}/video`。留空表示所有接口都需要凭证。`/api/v1/admin/*`、`/api/v1/auth/*` 与 `/api/setup/*` 无论如何都不会被开放，且没有任何覆盖开关。 | 做只读公开镜像或页面内嵌解析器时开放某一个接口。匿名调用方只拿到读权限，并按地址计量。 |

### `security` — 会放宽边界的那些（6 项，全部敏感）

每个默认值背后的理由见[安全](./15-security.md)。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `security.url_allowlist` | `[]` | 短链跳转时额外允许经过的主机名。内置平台列表始终生效；这里的每一条都按完整主机名精确匹配、不归属任何平台，只放宽展开，绝不影响路由。 | 只有当跳转链卡在某个未列出的中转主机上时才加。子域名不会被隐含包含；内网或回环名字会被拒绝。 |
| `security.cors_allow_origins` | `[]` | 允许调用本接口的浏览器来源。留空表示仅同源，且完全不发送 CORS 头。 | 填你自己前端的来源。填 `'*'` 等于把你的 API Key 交给用户访问的任何网站。 |
| `security.cors_allow_credentials` | `False` | 跨域请求是否允许携带 Cookie。只有在明确列出来源时才有意义——写了 `'*'` 时通配符优先、凭证被丢弃，因为浏览器本来也会拒绝这个组合。 | 只为具名来源的第一方前端打开。 |
| `security.enable_task_webhook` | `False` | 任务是否可以回调调用方提供的 `callback_url`。该 URL 是 SSRF 攻击面。 | 当每把 API Key 都在你掌控中、且想用推送代替轮询时打开。在提交与完成之间关掉它会静默跳过回调，这是安全的方向。 |
| `security.webhook_secret` | `''` | 用于给任务回调签名的共享密钥，以 `X-Dtk-Signature: sha256=<正文的 HMAC>` 头发送。 | 只要开了 webhook 就该设置。不设置的话，接收方无法区分真实通知和任何猜到回调地址的人。存储后展示为掩码。 |
| `security.request_proxy` | `'deny'` | 是否允许调用方通过 `?proxy=` 使用自己的出口。`deny` 直接拒绝该参数——功能关闭时是拒绝而不是忽略，因为静默丢弃参数会让请求从本服务器地址发出，而调用方以为走了自己的代理。`public` 仅允许公网可路由地址。`any` 连回环与内网地址也允许。 | 只有当每把 API Key 都被信任可以访问本实例所在网络时，`any` 才自洽；它是这份配置里单项放宽幅度最大的一个。 |

### `notify` — 告警（3 项）

在 **通知** 页编辑最省事，那里会在你添加通道时就做校验。参见[运维](./10-operations.md)。

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `notify.enabled` | `False` | 告警发送总开关。关闭后告警仍会记录，但不会发出。 | 确认某个通道可用之后再打开。 |
| `notify.channels` | `[]` | 告警的投递目标：一个描述列表，每项包含 `type`、`url`，以及可选的 `language`、`name`、`enabled`。支持的类型有 `webhook`、`bark`、`wecom`、`dingtalk`、`telegram` 与 `smtp`；`telegram` 用 `token` 和 `chat_id` 而不是 URL，`smtp` 需要 `host`、`sender` 与收件人。某一条格式错误只会被跳过并记录日志，不会连累其他通道。 | 增加或迁移通道时。里面的凭证读取时掩码、写入时保留，但目标发生变化的通道不会继承旧凭证。 |
| `notify.language` | `'en'` | 告警文本使用的语言；未单独指定语言的通道会使用它。 | 中文值班就设为 `zh`。 |

### `system` — 系统（1 项）

| 键 | 默认值 | 作用 | 什么时候需要改 |
| --- | --- | --- | --- |
| `system.check_updates` | `False` | 控制台是否提供版本更新检查。请求由你的浏览器发往 GitHub，服务端从不外联；之所以需要主动开启，是因为有些部署根本没有出网能力。 | 想知道有没有新版本时打开。 |

## 修改何时生效

一次写入会递增版本计数器，并在 Redis 频道上广播。执行写入的那个 API 进程会立即重载自己的快照——保存后仍显示旧值的控制台看起来就像有 bug。其他进程收到消息后重载；由于 pub/sub 在重连时可能丢消息，每个进程还会每 30 秒轮询一次版本计数器。pub/sub 是快路径，轮询才是保证最终收敛的那条。

快照是整体替换而不是逐字段修改的，因此正在处理中的请求会一直使用它开始时读到的那组值。

版本号可以在控制台标题栏（`settings v<n>`）、`dtk config list` 的末尾，以及配置 API 响应的 `version` 字段里看到。多人同时编辑时，靠这个数字判断你看到的是不是最新状态。

有两类变更不走这条路径：保留期窗口是由 worker 的维护轮次以 TimescaleDB 策略下发的，该轮次每 5 分钟跑一次；媒体清理也在同一轮次里进行。

## 配置表无法突破的限制

有些数字是代码里的常量，而且是刻意的。知道它们的存在，才知道一个配置项的作用边界在哪。

| 限制 | 值 | 位置 |
| --- | --- | --- |
| 主密钥最小长度 | 32 个字符 | `src/dtk/core/crypto.py` |
| 单次重新检查的上限 | 每轮 200 个作品，无论 `archive.recheck_batch` 写多少 | `src/dtk/worker/ops/archive_sweep.py` |
| 保留期窗口边界 | 两个超表策略均为 1–3650 天 | `src/dtk/ops/retention.py` |
| 快照压缩 | 7 天后压缩 | `src/dtk/db/models.py` |
| 关注列表调度 | 每 60 秒一次 | `src/dtk/worker/watcher.py` |
| 存在性扫描 | 每 6 小时排队一次 | `src/dtk/worker/watcher.py` |
| 维护轮次 | 每 5 分钟一次 | `src/dtk/worker/maintenance.py` |
| 身份补充轮次 | 每 60 秒一次 | `src/dtk/worker/pool_filler.py` |
| 签名风控阈值 | 20 次采样上 0.6，与 `sched.circuit_*` 无关 | `src/dtk/signing/registry.py` |

## 接下来读什么

- [安装与部署](./02-installation.md) —— 怎么写 `.env`、怎么把整套服务跑起来。
- [运维](./10-operations.md) —— 告警、备份，以及该盯着哪些指标。
- [安全](./15-security.md) —— 每一个敏感项默认值背后的理由。
- [故障排查](./14-troubleshooting.md) —— 包括「我改了配置但没生效」。
- [命令行参考](./13-cli.md) —— `dtk` 的其余命令。
