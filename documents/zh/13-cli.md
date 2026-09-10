# 命令行参考

`dtk` 是随应用镜像一起发布的运维与救援命令行工具。读完本文你会知道如何在运行中的部署里调用它、每条命令和每个选项的作用，以及哪些命令可以随手对线上实例执行、哪些只有在出事时才该动。

## 这个 CLI 是什么，不是什么

`dtk` **不是** REST API 的客户端。它直接连 Postgres，需要时再连 Redis，并且自己向平台发请求。这是刻意的设计：里面的每条命令都是控制台帮不上忙时你才需要的东西——没人能登录了、实例要迁到另一台机器、或者某个接口不出数据了而你需要看到没有鉴权、没有限流、没有缓存挡在中间的原始响应。

由此带来的几点后果值得直说：

- 只要 Postgres 可达，即使 api 容器挂了它也能用。
- 它绕过 API 密钥、权限范围、按密钥的限流以及响应缓存。它做的大部分事情都没有审计记录。
- `dtk fetch` 和 `dtk identity test` 会在调度器之外发出**真实的**平台请求。它们不申请租约、不受令牌桶约束，因此不会像 worker 那样自我限速。

如果你要的是带鉴权、限流和缓存的通路，请走 REST API，参见 [REST API 指南](./11-api.md)。

## 如何运行

### 在容器里运行（常规做法）

`api`、`worker`、`migrate` 三个服务用的是同一个镜像，镜像的 `PATH` 里就有 `dtk`。`docker compose exec` 直接执行命令、不经过镜像的 entrypoint，所以那三个角色参数（`api` / `worker` / `migrate`）在这里用不上：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk --version
```

```text
dtk 5.0.0
```

本页所有例子用的都是不带 `-T` 的 `exec`，其余文档也统一是这个写法。这一点对会提问的命令最要紧——`dtk user create`、`dtk user passwd` 和 `dtk backup restore` 都会提问，需要一个终端。只有写脚本、用管道或者往命令里重定向输入时才加 `-T`，就像下面那几段配方那样：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
```

```text
id        username   role   created                    last login
9bb3e8ad  evil0ctal  admin  2026-09-08T03:40:08+00:00  2026-09-08T03:40:09+00:00
```

用 `api` 还是 `worker` 都行——同一个镜像、同一份环境文件、同一个数据库。下面的例子统一用 `api`。

**容器的根文件系统是只读的。** 只有 `/tmp` 和挂在 `/var/lib/dtk/backups` 的 `backup-data` 卷可写。任何要写文件的命令都必须显式指向这两个位置。这是最常见的一个坑：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk backup create
```

```text
error cannot reach a dependency: [Errno 30] Read-only file system: 'backups'
```

解决办法是传一个可写路径，详见下面的 [`dtk backup`](#dtk-backup)。

### 在本地代码库里运行

```bash
uv sync --all-extras
uv run dtk --help
```

开发时、需要带 `--reload` 跑 `dtk serve` 时，或者容器根本起不来时，这是正确的用法。

麻烦在于连通性。`BootstrapSettings` 从**当前工作目录**读取 `.env`，所以 `uv run dtk` 要在仓库根目录执行。但 Compose 栈用的那份 `.env` 写的是容器内主机名：

```text
DTK_DATABASE_URL=postgresql+asyncpg://dtk:...@postgres:5432/dtk
DTK_REDIS_URL=redis://:...@redis:6379/0
```

`postgres` 和 `redis` 在宿主机上解析不了，而且 `docker/compose.yml` 是故意把它们放在 `internal: true` 网络上、不发布任何端口的。所以在宿主机上直接跑 CLI 连 Compose 栈会这样失败：

```bash
uv run dtk user list
```

```text
error cannot reach a dependency: [Errno 8] nodename nor servname provided, or not known
```

要么自己把那两个端口发布出来（并接受由此带来的暴露面，参见[安全](./15-security.md)），要么就用 `docker compose exec`。如果代码库连的是测试依赖或本机自己的 Postgres，在命令行上覆盖这两个 URL 即可：

```bash
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk@127.0.0.1:5432/dtk uv run dtk user list
```

没有 shell 补全。应用在构建 Typer app 时刻意关掉了补全功能，所以你找不到 `--install-completion` 这个选项。

## CLI 从环境里读什么

每条会访问状态的命令都会先走一次引导（bootstrap），从环境变量（以及工作目录下的 `.env`）读取 `DTK_*`。读不到时它会说清楚缺了什么并以退出码 1 结束，而不是甩一段 traceback：

```text
error DTK_SECRET_KEY must be set and at least 32 characters. Generate one with: openssl rand -base64 48
hint: export DTK_SECRET_KEY, or run the CLI inside the container that has it
```

| 变量 | 默认值 | 哪些命令需要 |
|---|---|---|
| `DTK_SECRET_KEY` | 无，必填，至少 32 个字符 | 除 `dtk --version`、`dtk migrate --show`、`dtk backup list` 之外的全部命令 |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | 所有访问状态的命令 |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | `dtk config set`、`dtk identity retire`、`dtk diagnose`、`dtk worker` |
| `DTK_BROWSER_RPC_URL` | 空（禁用） | `dtk identity mint` 必需；设置后，`dtk fetch`、`dtk identity test` 以及 `dtk diagnose` 的第 5 步会把它当作签名兜底 |
| `DTK_BIND_HOST` | `127.0.0.1` | `dtk serve` 未指定 `--host` 时 |
| `DTK_BIND_PORT` | `8000` | `dtk serve` 未指定 `--port` 时 |
| `DTK_LOG_LEVEL` | `info` | `dtk serve`（传给 uvicorn，它启动的 API 应用也会再读一次）和 `dtk worker`——worker 会用它配置日志栈。它**不会**改变其他命令的日志输出——`fetch`、`diagnose`、`config`、`identity`、`proxy`、`user`、`backup`、`migrate` |
| `DTK_LOG_JSON` | `true` | `dtk worker` 以及 `dtk serve` 启动的应用：`true` 时日志行渲染为 JSON，`false` 时渲染为人类可读的控制台格式。其他命令不配置日志栈，所以它在那里不起作用 |

`DTK_BACKUP_DIR` 被刻意排除在上表之外。API 和 worker 会读它，而 CLI 的 `dtk backup create` 和 `dtk backup list` 不读——它们默认使用字面量相对路径 `backups`。见 [`dtk backup`](#dtk-backup)。

有三条命令需要的东西比其他命令少，这正是它们在坏掉的机器上还能用的原因：

- `dtk --version` 什么都不需要。
- `dtk migrate --show` 只读磁盘上的迁移脚本，不建立任何连接，所以没有密钥、没有数据库也能跑。
- `dtk backup list` 只读一个目录，同样不需要密钥和数据库。

## 输出、退出码与机器可读输出

只有三个退出码，进程不会返回别的值：

| 退出码 | 含义 |
|---|---|
| `0` | 命令完成了它该做的事 |
| `1` | 命令跑了但失败了——数据库不可达、用户不存在、代理不通、平台返回了风控 |
| `2` | 调用方式不对——未知选项、非法取值、缺参数、未知的配置键 |

把 `1` 和 `2` 分开，是为了让部署脚本能区分“工具被调用错了”和“系统坏了”。命令组不带子命令执行（`dtk`、`dtk identity`）会打印帮助并以 `2` 退出。

数据走 stdout，`ok` 和 `info` 这类行同样走 stdout；只有 `warn` 和 `error` 走 stderr。所以把 stderr 重定向掉并不能让管道里只剩数据——命令成功时那行 `ok` 仍然会写进去。CLI 打印的每个字符串都先经过脱敏：代理密码、Cookie 值、机器人 token、签名参数在输出前都会被掩码——终端是最不隐私的地方，而这些输出本来就是拿去贴进 issue 的。

### 怎么拿到干净的 JSON

有两条命令支持 `--json`：`dtk fetch` 和 `dtk diagnose`。有三件事挡在“直接管道给 `jq`”前面：

1. **日志行也打在 stdout 上。** `dtk fetch` 和 `dtk diagnose` 没有配置日志栈，于是 `structlog` 用它自己的默认设置写 stdout，和你的数据混在一起。`DTK_LOG_LEVEL` 压不住它们。JSON 文档从第 0 列的 `{` 开始，所以可以用 `sed` 把它切出来。
2. **没有终端时 rich 按 80 列折行**，这会在长 JSON 字符串中间插入换行，把文档变得无法解析。把 `COLUMNS` 设大。
3. **`dtk diagnose --json` 会把状态行打在 JSON 之后**，而且也在 stdout 上。所有步骤都通过的那次运行会以 `ok  all steps passed` 结尾；`-o` 同样会追加一行 `text report written to ...`。只出现 warning 的运行则不会——因为 warning 走 stderr，这也正是它容易被忽略的原因。把结尾这行也去掉。

合在一起：

```bash
docker compose -p dtk -f docker/compose.yml exec -T -e COLUMNS=10000 api \
  dtk diagnose --skip-smoke --json 2>/dev/null | sed -n '/^{/,$p' | sed '/^ok  /d'
```

这样就能解析了。不发网络请求的命令——`dtk config get`、`dtk migrate --show`、`dtk user list`——不会产生日志行，两个技巧都用不上。

## 命令总览

| 命令 | 作用 | 涉及 |
|---|---|---|
| `dtk fetch` | 解析一个链接并打印归一化后的结果 | 数据库、平台 |
| `dtk diagnose` | 六步自检，输出一份报告 | 数据库、Redis、代理、browser-rpc、平台 |
| `dtk worker` | 前台运行任务 worker | 数据库、Redis |
| `dtk migrate` | 执行数据库迁移 | 数据库 |
| `dtk serve` | 运行 API 服务 | — |
| `dtk user create/passwd/list` | 控制台账号 | 数据库 |
| `dtk backup create/list/restore` | 备份与恢复整个实例 | 数据库、文件系统 |
| `dtk config list/get/set` | 存在数据库里的运行时配置 | 数据库，`set` 还需 Redis |
| `dtk identity list/mint/retire/test` | 身份池 | 数据库、browser-rpc、平台 |
| `dtk proxy list/add/import/test` | 出口代理 | 数据库、网络 |

根命令只有 `--version` 和 `--help` 两个选项。

## `dtk fetch`

解析一个链接并打印返回结果。要回答“这个接口是不是死了”，这是最快的办法：不用登录、不用 API 密钥、不限流、不走缓存——URL 被解析，一条带签名的请求通过一个真实身份发出去，然后把答案打出来。

```text
dtk fetch [OPTIONS] {url}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `url` | 字符串，必填 | — | 抖音或 TikTok 链接，或者粘贴过来的分享文案 |
| `--raw` | 开关 | 关 | 输出中一并带上平台自己的原始 payload |
| `--json` | 开关 | 关 | 只打印 JSON 结果，便于管道处理 |
| `--identity` | 字符串 | 下一个身份 | 指定使用某个身份。**必须是完整 UUID**，不能用表格里那 8 位前缀 |
| `--proxy` | 字符串 | 无 | 仅用于短链展开那一跳的代理，见下 |
| `--timeout` | 浮点数 | `25.0` | 等待平台响应的秒数 |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk fetch "https://www.tiktok.com/@owlcitymusic/video/7218694761253735723"
```

```text
field        value
platform     tiktok
resource     video
endpoint     tiktok.content_detail
url          https://www.tiktok.com/@owlcitymusic/video/7218694761253735723
identity     af4a98f6
proxy        -
http status  200
outcome      ok
rule         default.ok
latency ms   343
bytes        24670
{
  "platform": "tiktok",
  "content_id": "7218694761253735723",
  "kind": "video",
  ...
}
ok  endpoint answered
```

在依赖它之前值得知道这几点：

- **`--proxy` 不是请求代理。** 它只作用于展开短链（`v.douyin.com/...`）的那一跳。平台请求本身走的是所选身份绑定的那个代理，也就是摘要里 `proxy` 那一行显示的东西。
- **不指定身份时，它挑的是该平台最久未使用的那个**——也就是调度器下一个会拿的那个，而不是最健康的那个。
- **只要 outcome 不是 `ok` 就退出 1**，包括“视频已删除”这类业务错误。`dtk identity test` 把业务错误算作通过，`fetch` 不算——因为你要的是数据，而你没拿到。
- **它会归档解析结果**，规则和 worker 完全一致，前提是 `archive.enabled` 打开（默认打开）。所以 fetch 并非只读：它可能往 `content_snapshots` 写一行。归档失败会被记日志并忽略，不会让命令报错。
- **解析失败时 `--raw` 才是重点。** `UPSTREAM_CHANGED` 错误会指出哪个字段不见了，而旁边的原始响应体正是判断“平台改名了”还是“平台干脆不返回了”的依据：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw "<url>"
```

链接只可能解析成两种东西：一个视频，或一个用户主页。直播间、音乐页之类 URL 解析器认得，但没有对应接口，命令会明确说是哪种资源被拒绝了，而不是给一个笼统的解析失败。

## `dtk diagnose`

一条命令搞定的自检，也是实例起来了但不出数据时第一个该跑的东西。它跑的是与控制台完全相同的六个步骤，输出一份专门设计成可以贴进 issue 的报告——报告在打印之前已由 ops 层脱敏，不需要你自己动手。

```text
dtk diagnose [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--json` | 开关 | 关 | 以 JSON 而非表格输出报告 |
| `--output`、`-o` | 路径 | 无 | 同时把文本报告写入文件 |
| `--skip-smoke` | 开关 | 关 | 不做端到端请求 |
| `--platform` | `douyin` \| `tiktok` | `douyin` | 冒烟测试用哪个平台 |
| `--smoke-url` | 字符串 | 该平台内置链接 | 冒烟测试使用的链接 |
| `--probe-url` | 字符串 | `https://api.ipify.org?format=json` | 报告代理出口地址的服务。注意这**不是** `dtk proxy test` 用的默认值，后者是 `https://ipinfo.io/json` |

六个步骤，按顺序：

| # | 步骤 | 检查什么 |
|---|---|---|
| 1 | `components` | Postgres、Redis、browser-rpc 是否可达 |
| 2 | `egress` | 本机能否直连平台域名 |
| 3 | `proxies` | 逐个代理：连通性和出口 IP。它显示的国家是代理行上已经存好的那个，不是新做一次 GeoIP 查询 |
| 4 | `pool` | 有多少可用身份，最老的一个有多陈旧 |
| 5 | `signing` | 纯 Python 签名与 browser-rpc 的签名逐平台比对——就是生产环境里 registry 跑的那套影子对比 |
| 6 | `smoke` | 一条固定的公开链接走完整条流水线 |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --skip-smoke
```

```text
#  step        status   reason
1  components  pass     all components reachable
2  egress      pass     all platform domains reachable
3  proxies     warn     no proxies configured
4  pool        pass     18 active identities
5  signing     pass     native and browser signatures agree
6  smoke       skipped  no smoke test link configured
proxies: Every identity will share this machine's egress IP. That is fine for a
first look and a correlation risk for a busy pool.
warn 1 step(s) raised a warning
```

写脚本时需要注意的行为：

- **warning 不会让命令失败。** 只要有步骤 fail 就退出 1；warning 打到 stderr，退出码仍是 0。因为一个 warning 就让命令失败，会把定时健康检查变成运维学会无视的误报。
- **跑不了的步骤是 skipped，不是 failed。** 没配 browser-rpc 意味着第 5 步是 `skipped`——你从来没装过的可选依赖不算故障。
- **它不写数据库。** 第 3 步会探测代理，但不像 `dtk proxy test` 那样把健康状态写回去。
- **第 5 步第一次很慢。** 比对要驱动真实浏览器页面，所以冷启动 browser-rpc 之后的第一个签名以秒计，之后每一次都是毫秒级。代码里记录的数字是冷绑定约 4 秒、热绑定约 10 毫秒；而 browser-rpc 自己的上限——开上下文 45 秒加签名 8 秒——正是签名超时定为 60 秒的原因。
- **`-o` 需要可写路径。** 在容器里就是 `/tmp/report.txt` 或者 `/var/lib/dtk/backups` 下面的路径。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk diagnose -o /tmp/dtk-report.txt
docker compose -p dtk -f docker/compose.yml exec api cat /tmp/dtk-report.txt
```

JSON 形式包含 `version`、`started_at`、`finished_at`、`passed`、`verdict`（`pass` / `warn` / `fail`）、一个 `steps` 数组，以及一个预渲染的 `text` 字段，内容就是那份纯文本报告。

如何解读输出：[故障排查](./14-troubleshooting.md)。

## `dtk worker`

运行任务 worker，直到被停止。

```text
dtk worker
```

它刻意不提供任何选项。并发度、任务领取超时、后台任务间隔属于进程配置，不属于调用方式——同一台机器上两个 worker 对自己的限额各执一词，是从外面完全看不出来的问题。它使用的值是代码常量：并发 4、队列领取超时 5 秒、任务失败前重试 3 次、收到 `SIGTERM` 后有 60 秒排空在途工作。

这就是 `worker` 容器以 `python -m dtk.worker` 运行的同一个进程。只有一份实现，正是“出事时能手动拉起一个 worker”这件事的全部意义：它的行为和 Compose 里的那个完全一致。

```bash
uv run dtk worker
```

和 Compose 的 worker 并存是合法的——它们消费同一个 Redis 队列，就是共享而已。吞吐的瓶颈是身份池而不是 worker 数量，所以在同一个池上再开一个 worker，收益远比看上去小。真要扩容请用 Compose：

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=2
```

`Ctrl-C` 会打印 `stopped` 并以 0 退出。

## `dtk migrate`

执行数据库迁移。

```text
dtk migrate [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--revision` | 字符串 | `head` | 目标版本；`head` 表示全部应用 |
| `--show` | 开关 | 关 | 打印磁盘上的版本号后退出 |

正常情况下这是 `migrate` 容器在启动时做的事，而且 `api` 和 `worker` 必须等它成功退出后才会启动。手动执行的场景是：需要挑时间做的升级，或者从旧 schema 的备份恢复出来的数据库。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate --show
```

```text
field             value
revision on disk  0007
```

`--show` 读的是打包在 package 里的迁移脚本。它不建立数据库连接、不读配置，所以在其他一切都不工作的机器上，它仍能回答“这个镜像期望的是哪个 schema”。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate
```

```text
ok  database migrated to head (head on disk: 0007)
```

这里没有回滚。`--revision` 是传给 upgrade 的，所以指定一个更早的版本并不会降级到它。降级在代码里存在，但没有暴露到命令行，这是刻意的拒绝：从一次失败升级中安全退回的办法，是升级前做的那份备份。

## `dtk serve`

运行 API 服务。

```text
dtk serve [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--host` | 字符串 | `DTK_BIND_HOST`，其本身默认 `127.0.0.1` | 监听地址 |
| `--port` | 整数 | `DTK_BIND_PORT`，其本身默认 `8000` | 监听端口 |
| `--reload` | 开关 | 关 | 源码变更时热重载 |
| `--workers` | 整数，1–32 | `1` | 工作进程数 |

这是开发与救援用的入口。容器是通过自己的 entrypoint 脚本启动 uvicorn 的，那个脚本额外加了 `--no-server-header` 以及反向代理需要的 forwarded 头处理；`dtk serve` 不加这些。请在代码库里用它，而不是拿它替代 `api` 容器。

```bash
uv run dtk serve --reload
```

`--reload` 和 `--workers` 互斥，而且这个调用在读取环境之前就被拒绝了——不能因为恰好 `DTK_SECRET_KEY` 也没设，就把一次错误的调用报成运行时失败：

```bash
uv run dtk serve --reload --workers 2
```

```text
Usage: dtk serve [OPTIONS]
Try 'dtk serve --help' for help.
╭─ Error ──────────────────────────────────────────────────────────────────────╮
│ Invalid value: --reload cannot be combined with --workers                     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

退出码 2。

## `dtk user`

控制台账号：创建、重置密码、列出。

自托管的工具没有找回密码邮件，也没有客服，所以忘了控制台密码的管理员只有一条路可以回去：机器上的一个 shell。这就是这个命令组存在的理由——没有它，诚实的说明只能是“把数据库删了重来”，为了一个忘掉的字符串扔掉所有身份、所有快照和所有配置。

密码用 argon2id 按 OWASP 推荐参数哈希（19 MiB 内存、2 轮迭代、1 条并行通道）。密码本身和它的摘要永远不会被打印、回显或记录。最短 8 个字符，和控制台的下限一致。

### `dtk user create`

```text
dtk user create [OPTIONS] {username}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `username` | 字符串，必填 | — | 登录名，实例内唯一 |
| `--role` | `admin` \| `operator` \| `viewer` | `admin` | 权限级别 |
| `--stdin` | 开关 | 关 | 从 stdin 读密码，而不是交互提示 |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user create alice --role operator
```

它会两次提示输入密码（不回显，需确认），然后打印新账号的 id、用户名和角色。

给自动化脚本准备的是 `--stdin`。它只读一行，所以 here-doc 塞不进第二条命令；它还会同时去掉结尾的 `CR` 和 `LF`——否则从 CRLF 文件里管道进来的密码会带着回车被哈希，之后没有任何人能输对。

```bash
read -rs -p 'password: ' DTK_NEW_PASSWORD; echo
printf '%s\n' "$DTK_NEW_PASSWORD" | docker compose -p dtk -f docker/compose.yml \
  exec -T api dtk user create alice --role operator --stdin
unset DTK_NEW_PASSWORD
```

用 `read -rs` 而不是把密码直接敲在命令行上，可以让它不进 shell 历史。如果用户名已存在，命令以退出码 1 失败，并提示你去用 `dtk user passwd`。

### `dtk user passwd`

```text
dtk user passwd [OPTIONS] {username}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `username` | 字符串，必填 | — | 要重置的账号 |
| `--stdin` | 开关 | 关 | 从 stdin 读密码，而不是交互提示 |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd evil0ctal
```

```text
ok  password changed for evil0ctal
```

这条命令刻意不依赖 Redis。缓存连不上和重置密码毫无关系，把它做成硬依赖等于砍掉这条命令存在的救援通路。

### `dtk user list`

```text
dtk user list
```

没有选项。列：`id`（截断到 8 位）、`username`、`role`、`created`、`last login`。永远不显示密码摘要。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
```

```text
id        username   role   created                    last login
9bb3e8ad  evil0ctal  admin  2026-09-08T03:40:08+00:00  2026-09-08T03:40:09+00:00
```

没有 `dtk user delete`。删除账号是控制台的操作，参见[用户与 API 密钥](./09-users-and-api-keys.md)。

## `dtk backup`

创建、查看和恢复备份。归档格式、manifest、密钥校验和行编码都在共享的 ops 层里，所以控制台和 CLI 不可能产出互不兼容的归档。

这个格式有两点几乎会让所有人意外，所以放在命令之前说：

- **凭据是加密导出的。** 恢复需要**同一个** `DTK_SECRET_KEY`。没有它，归档里的 Cookie 和代理 URL 无法解密，恢复会被直接拒绝而不是做一半。
- **身份默认不导出。** 它们绑定着一个代理和一个出口地址，而新机器上并没有这些。

### 默认目录不是 `DTK_BACKUP_DIR`

`dtk backup create -o` 和 `dtk backup list --dir` 的默认值都是字面量相对路径 `backups`，相对于当前工作目录解析。API 和 worker 读 `DTK_BACKUP_DIR`，而这两条命令不读。容器里的工作目录是只读的 `/app`，所以不带参数的 `dtk backup create` 会失败，不带参数的 `dtk backup list` 会看错地方。请始终显式传路径：

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
```

`/var/lib/dtk/backups` 就是 `docker/compose.yml` 挂载共享卷 `backup-data` 的位置，也是它把 `DTK_BACKUP_DIR` 指向的位置，所以写在那里的归档才是控制台能列出来的那些。

### `dtk backup create`

```text
dtk backup create [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--output`、`-o` | 路径 | `backups` | 归档文件路径，或者要写入的目录 |
| `--include-identities` | 开关 | 关 | 同时导出登录凭据；用于迁移部署，不适合日常备份 |

如果路径是目录，或者不以 `.tar.gz` 结尾，就会在其中生成带时间戳的文件名：`dtk-backup-20260910T084830Z.tar.gz`。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
```

```text
table              rows
api_keys           31
content_snapshots  138
proxies            0
settings           4
users              1
ok  wrote /var/lib/dtk/backups/dtk-backup-20260910T084830Z.tar.gz (0.0 MiB)
identities were not exported; pass --include-identities when moving the whole
deployment to a machine with the same egress
```

哪些表进去，哪些不进去：

| 表 | 何时导出 |
|---|---|
| `users`、`api_keys`、`proxies`、`settings`、`content_snapshots` | 总是 |
| `identities` | 仅在 `--include-identities` 时 |
| `request_log`、`identity_events`、`tasks`、`audit_log`、`settings_version` | 从不 |

被排除的那一组不是疏漏：`request_log` 和 `identity_events` 是运维历史，换台机器就毫无意义；`tasks` 是在途工作；`audit_log` 记录的是针对那个正被替换掉的实例所做的操作。

归档文件及其内部每个成员都以 `0600` 权限写入。凭据列是密文，但 `settings` 里存着明文的告警渠道 webhook，所以无论是归档文件本身还是手工解出来的副本，都不能让机器上的其他账号读到。

代价：整个数据库会被导成 JSON-lines 再 gzip。`content_snapshots` 很大时，这是几分钟的 CPU 和一个可能几 GB 的文件。gzip 跑在工作线程上，不会卡住进程；暂存目录建在归档文件**旁边**而不是 `/tmp`——容器里的 `/tmp` 是 64 MB 的 tmpfs，也就是内存，稍有规模的归档就会把它撑爆。

### `dtk backup list`

```text
dtk backup list [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--dir` | 路径 | `backups` | 要扫描的目录 |

按时间倒序。列：`file`、`created`、`version`（写入它的 dtk 版本）、`rows`、`identities`、`size`、`state`。损坏的归档也会列出来，`state` 为 `error` 并以 warning 打印原因——对一个正在检查备份的人来说，把它藏起来正是最不该做的事。

这条命令不建立数据库连接，也不需要 `DTK_SECRET_KEY`，所以在其他一切都不工作的机器上它照样能用。

### `dtk backup restore`

```text
dtk backup restore [OPTIONS] {path}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `path` | 路径，必填 | — | 要恢复的归档 |
| `--yes`、`-y` | 开关 | 关 | 不询问确认 |

它会先打印 manifest——创建时间、写入它的版本、schema 版本、是否含身份、行数——然后在写任何东西之前询问确认。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/dtk-backup-20260910T084830Z.tar.gz
```

**已存在的行会被保留。** 恢复是把缺的补上，而不是覆盖一个已经在用的实例。这让它在“不会毁数据”的意义上可以对线上实例执行——但也意味着它**不是**回滚手段：把旧归档恢复到更新的数据库上，只会把删掉的行加回来，别的什么都不改。

密钥不匹配时，命令在写入前就停下：

```text
error DTK_SECRET_KEY does not match the key this backup was taken with
hint: restore with the original key; without it the cookies and proxy URLs in the archive cannot be decrypted
```

归档 schema 版本 1 和 2 都能读；更新的版本会被拒绝，而不是被误读。

## `dtk config`

存在数据库里的运行时配置。

实例初始化之后，`settings` 表就是权威来源，所以一个配错的值**不能**靠改 `.env` 加重启来修——这一点每个人都会被坑一次。这组命令就是在没有控制台的情况下修它的办法。运行时配置共有 54 项，完整目录及其含义和默认值在[配置参考](./03-configuration.md)。

### `dtk config list`

```text
dtk config list [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--scope` | `bootstrap` \| `env_only` \| `runtime` \| `sensitive` | 全部 | 只显示该作用域的配置 |
| `--changed` | 开关 | 关 | 只显示与默认值不同的配置 |

列：`key`、`value`、`default`、`scope`、`description`。与默认值不同的值会加粗显示。末尾会打印 settings 版本号——运行中的进程就是靠这个计数器知道配置变了。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed
```

```text
no settings match that filter
settings version 112
```

关于作用域有两点。54 项配置里有 47 项是 `runtime`、7 项是 `sensitive`；`--scope bootstrap` 和 `--scope env_only` 能传，但永远匹配不到任何东西，因为那些配置只存在于环境变量里，从不进这张表。

另外，**取值是普通字符串的 `sensitive` 配置会被掩码。** 这是按声明而不是按形状决定的：注册表说了哪些配置敏感，然后每个展示面——这张表、admin API、写操作留下的审计记录——都用同一套规则掩码。掩码会保留前四个字符、其余替换成 `***`；长度不超过四个字符的值则整体变成 `***`，因为四个字符里露出三个算不上掩码。所以 `security.webhook_secret` 设成 `s3cr3t-value-abcdef` 时显示为 `s3cr***`，而 `security.request_proxy` 在默认值 `deny` 下显示为 `***`、改成其他可选值后则显示为 `allo***` 或 `publ***`。它既适用于真正的机密，也适用于 `security.request_proxy` 这种本身并不是机密的敏感配置。列表、布尔值和数字照原样显示，空字符串也照原样显示为空，因为“没设置”这件事值得看得见。

### `dtk config get`

```text
dtk config get {key}
```

以 JSON 打印单个配置。未知的键属于调用错误，退出码 2。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config get cache.content_ttl
```

```json
{
  "key": "cache.content_ttl",
  "value": 1800
}
```

### `dtk config set`

```text
dtk config set [OPTIONS] {key} {value}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `key` | 字符串，必填 | — | 配置键 |
| `value` | 字符串，必填 | — | 新值；列表用逗号分隔 |
| `--yes`、`-y` | 开关 | 关 | 修改敏感配置时不询问 |

值会先按声明的类型转换并校验，**然后**才写入；类型不匹配属于调用错误，退出码 2。写入之后会递增 settings 版本号，并在 API 与 worker 都在监听的 Redis 频道上广播，于是运行中的部署无需重启就能生效。这是 `config` 组里唯一需要 Redis 的子命令。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk config set cache.content_ttl 900
```

```text
ok  cache.content_ttl = 900
```

敏感配置会扩大攻击面——URL 白名单、CORS 来源、调用方自带的请求代理——所以修改前会先询问确认。脚本里可以用 `-y` 跳过；不要养成顺手加的习惯。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk config set security.url_allowlist "example.com,cdn.example.com" -y
```

没有 `dtk config reset`。要把某项配置恢复默认，就把 `dtk config list` 显示的默认值显式设回去，或者在控制台里清掉这条覆盖。

## `dtk identity`

查看、铸造、退休和探测身份池里的身份。身份是什么、身份池为什么长成这样，见[身份与代理](./06-identities-and-proxies.md)；本节只讲命令。

这些输出里永远不会出现 Cookie 罐。`list` 一点都不显示，`mint` 只报告回来了哪些 Cookie **名字**，`test` 报告的是平台答了什么——而不是发出去了什么。

`retire` 和 `test` 既接受完整 UUID，也接受表格里打印的 8 位前缀；前缀有歧义时会被拒绝而不是猜一个。`mint --proxy` 和 `fetch --identity` 需要**完整**的 id。

### `dtk identity list`

```text
dtk identity list [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--platform` | `douyin` \| `tiktok` | 全部 | 只看这个平台 |
| `--state` | `minting` \| `active` \| `cooling` \| `degraded` \| `retired` | 全部 | 只看处于该状态的身份 |
| `--limit` | 整数，1–500 | `50` | 显示行数 |

列：`id`、`platform`、`state`、`auth`（该身份是否已登录）、`source`（`minted` 或 `imported`）、`browser`、`proxy`（代理标签，或 `direct`）、`fails`（连续失败次数）、`cooldown`、`last used`。

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity list --platform tiktok --state active --limit 10
```

这张表有十列，终端一窄就会折得很难看——而在根本没有终端时（加了 `-T`，或者从 cron 里跑），rich 会退回 80 列，那就完全没法看了。想让它宽一点就设 `COLUMNS`：

```bash
docker compose -p dtk -f docker/compose.yml exec -e COLUMNS=200 api dtk identity list
```

### `dtk identity mint`

```text
dtk identity mint [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--platform` | `douyin` \| `tiktok`，**必填** | — | 为哪个平台铸造 |
| `--count` | 整数，1–20 | `1` | 铸造几个 |
| `--proxy` | 字符串（完整 UUID） | 一个空闲代理 | 指定代理，而不是自动挑一个空闲的 |

需要 `DTK_BROWSER_RPC_URL`。没有它时命令以退出码 1 失败，并告诉你去设置它或者改为导入 Cookie：

```text
error browser-rpc is not configured
hint: set DTK_BROWSER_RPC_URL, or import cookies instead
```

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity mint --platform douyin --count 2
```

输出列：`id`（完整）、`proxy`、`exit ip`、`cookies`（只有名字）。

每个身份终身绑定一个代理。不指定 `--proxy` 时，会挑一个在该平台上还没有身份的代理——两个身份在同一平台共用一个出口地址，正是身份池要避免的那种关联。如果一个代理都没有，身份会以 `direct` 铸造出来——能用，但等池子忙起来就是关联风险。

代价：铸造要驱动一个真实的无头浏览器。`browser-rpc` 容器是整个栈里最重的东西（实测预热状态下常驻 2.57 GiB、CPU 占用相当于六个核），`--count 20` 就是二十次页面加载。这不是能放进循环里跑的命令。

### `dtk identity retire`

```text
dtk identity retire [OPTIONS] {identity_id}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `identity_id` | 字符串，必填 | — | 身份 id，完整或表格里的前缀 |
| `--reason` | 字符串 | `retired from the cli` | 记录在身份事件上的原因 |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity retire 9d8daf26 --reason "cookies rejected since the 9th"
```

**这会立即清空 Cookie 罐，且不可逆。** 没有“取消退休”；已退休的身份就是死的，池子只能铸造或导入一个替代品。

对已经退休的身份再次退休会被拒绝，而不是重复执行，这个拒绝是有意的：第二次调用会把记录下来的原因和时间戳覆盖成 `retired from the cli`，从而抹掉这个身份当初到底为什么被拿出池子的记录。

这个子命令需要 Redis，因为退休要广播给正持有该身份传输客户端的运行中进程。

### `dtk identity test`

```text
dtk identity test [OPTIONS] {identity_id}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `identity_id` | 字符串，必填 | — | 身份 id，完整或表格里的前缀 |
| `--url` | 字符串 | 该身份所属平台的内置冒烟链接 | 换用这个链接做探测 |
| `--timeout` | 浮点数 | `25.0` | 请求秒数 |

以该身份发出一条真实的带签名请求，并报告返回结果：`identity`、`platform`、`endpoint`、`proxy`（已掩码）、`cookies`（只有名字）、`ok`、`outcome`、`status`、`latency ms`、`rule`、`detail`。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk identity test 9d8daf26
```

不申请租约、不限流、不记账：这个探测回答的是平台还愿不愿意搭理这个身份，并且什么都不改。探测一个身份，绝不能成为让它进入冷却的原因。

**业务错误算作通过。** 如果内置链接指向的帖子后来被删除或设为私密，平台会给出原因，而那就是一个真实答案，证明这个身份是好的。只有在压根拿不到可用答案时——风控，或者网络故障——命令才会退出 1。

## `dtk proxy`

代理：列出、添加、导入和探测。

代理 URL 自带凭据，所以这一列是加密存储的，而且每一次渲染都要过掩码。明文只在一次探测持续的时间里存在。

允许的协议是 `http`、`https`、`socks5` 和 `socks5h`；其他的要么是笔误，要么是从网页上抄来的。URL 必须带主机和端口。

协议和主机这两类拒绝是刻意不引用输入内容的。`urlsplit` 把第一个冒号之前的全部内容当作协议，所以一行没写协议就粘过来的供应商记录——`user:password@host:3128`——会把账号名，粘贴顺序不巧的话还有密码，写进错误信息里。

**但有一类拒绝确实会引用输入，值得知道。** 端口检查读的是 `urlsplit(...).port`，当最后一个冒号后面的文本不是数字时，消息由 `urllib` 自己抛出，并且带上了那段内容：粘成 `http://myuser:hunter2` 的一行会被拒绝为 `Port could not be cast to integer value as 'hunter2'`——那正是密码。这条消息会打到终端上，`dtk proxy import` 也会把它连同行号一起打印。在源头修掉之前，请把拒绝信息当成可能携带凭据的东西对待。

### `dtk proxy list`

```text
dtk proxy list [OPTIONS]
```

| 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `--healthy` | 开关 | 关 | 只看上次探测通过的代理 |

列：`id`、`label`、`url`（凭据已掩码——主机和端口保留，用户名最多保留两个字符）、`country`、`timezone`、`state`、`last check`。

`state` 在代理被探测过一次之前是 `unchecked`，之后是 `healthy` 或 `failed`。底层那一列默认为 true，否则一个从未探测过的代理会显示成 healthy，旁边却写着上次检查时间是 `never`。

### `dtk proxy add`

```text
dtk proxy add [OPTIONS] {url}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `url` | 字符串，必填 | — | 代理 URL，例如 `http://user:pass@host:3128` |
| `--label` | 字符串 | 无 | 列表里显示的名字 |
| `--country` | 字符串 | 无 | 出口的 ISO 国家代码 |
| `--timezone` | 字符串 | 无 | 出口的 IANA 时区 |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk proxy add "http://user:pass@198.51.100.7:3128" --label "vendor-a-01" --country US --timezone America/New_York
```

URL 在进数据库之前就被加密，打印回来的只有掩码形式。

`--country` 和 `--timezone` 描述的是出口，在这个代理上铸造身份时会作为地理提示使用，避免浏览器指纹声称的语言区与它的 IP 地址自相矛盾。你不必手工填：`dtk proxy test` 会从探测结果里学到这两项并写回去。

把 URL 直接写在命令行上会让凭据进入 shell 历史。稍微重要一点的场合，请改用 `dtk proxy import` 配文件。

### `dtk proxy import`

```text
dtk proxy import {path}
```

批量通道。代理供应商给的是一个文本文件，而把四十行手动敲进 `add`，正是人们最后干脆把整个文件粘进聊天窗口的原因。

格式：一行一个代理 URL，后面可以跟一个或多个**空格**和一个标签。空行和以 `#` 开头的行会被跳过，这样供应商的备注也能一起保留下来。URL 和标签之间不支持用制表符分隔——切分只认第一个空格，用制表符分隔的行会把标签并进端口里而被拒绝。如果供应商的文件用的是制表符，先转换一下（`tr '\t' ' ' < vendor.txt > proxies.txt`）。

```text
# vendor A, residential, delivered 2026-09-01
http://user:pass@198.51.100.7:3128    vendor-a-01
http://user:pass@198.51.100.8:3128    vendor-a-02
socks5://user:pass@203.0.113.9:1080   vendor-a-03
```

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk proxy import /tmp/proxies.txt
```

```text
id                                    url
b1f0c2d4-8e3a-4f51-9c07-2ad61e5b4c99  http://us***:***@198.51.100.7:3128
c3e2a7f1-55b9-4d0e-8a63-7f19c4d2e880  http://us***:***@198.51.100.8:3128
ok  added 2, skipped 1 duplicate(s), rejected 0 line(s)
```

去重是拿已存储 URL 解密后逐一比对的，所以重新导入供应商更新过的文件只会新增真正新的那些。被拒绝的行报告的是**行号和原因**，而不是把整行回显出来。协议不对或缺主机时，原因里不含任何输入内容——但端口不对时不是这样：那条消息是 `urllib` 自己给出的，会引用它解析不了的那段文本，而在凭据没带 `@` 就粘上去的行里，那段文本就是密码。把拒绝列表贴进工单之前，请先看上面 [`dtk proxy`](#dtk-proxy) 里的那条提醒。

要把文件送进容器，用管道灌进去。`docker compose cp` 对 `/tmp` 不管用：容器的根文件系统被标记为只读时，daemon 会拒绝一切目标不在卷里的拷贝，而 tmpfs 不是卷——往 `/tmp` 拷会失败并报 `Error response from daemon: container rootfs is marked read-only`。拷到 `/var/lib/dtk/backups` 下则没问题，因为那里是 `backup-data` 卷，[运维](./10-operations.md)里的恢复演练靠的正是这一点。要送进 `/tmp`，就把内容重定向给容器里的一个 shell：

```bash
docker compose -p dtk -f docker/compose.yml exec -T api sh -c 'cat > /tmp/proxies.txt' < ./proxies.txt
docker compose -p dtk -f docker/compose.yml exec api dtk proxy import /tmp/proxies.txt
docker compose -p dtk -f docker/compose.yml exec api rm -f /tmp/proxies.txt
```

像上面那样用完就删：`/tmp` 虽然是内存，但在容器重启之前，这个文件里的代理密码都是明文。

### `dtk proxy test`

```text
dtk proxy test [OPTIONS] {proxy_id}
```

| 参数 / 选项 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `proxy_id` | 字符串，必填 | — | 代理 id，完整或表格里的前缀 |
| `--probe-url` | 字符串 | `https://ipinfo.io/json` | 报告出口地址的服务 |
| `--write` / `--no-write` | 开关 | `--write` | 把结果写回代理这一行 |

报告 `id`、`state`、`latency ms`、`exit ip`、`country`、`timezone`、`detail`。探测超时是 10 秒，没有做成选项。

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk proxy test b1f0
```

默认会把结果写回去：健康标志总是写，国家和时区在探测得到时写。`--no-write` 让它变成纯读取——当你测的是探测服务而不是代理时，这才是你要的。

`--probe-url` 存在是因为 `ipinfo.io` 并非处处可达。任何返回带 `ip`、`country`、`timezone` 字段的 JSON 对象的服务都可以。

**有一个会浪费你一小时的坑：**探测是用 `httpx` 发的，而 `httpx` 要说 SOCKS 需要可选的 `socksio` 包。这个包不在本项目的依赖里，所以一个被 `dtk proxy add` 接受了的 `socks5://` 代理，探测结果会是**失败**，detail 里写着缺少那个包。真正的平台请求并不走 `httpx`——它们走的是另一个自己会处理 SOCKS 的 HTTP 客户端——所以这个代理很可能是好的。把 SOCKS 探测失败读作“探测给不了你答案”，而不是“代理死了”。

## 线上安全执行的命令，与救援命令

### 只读——随时可以跑

下面这些既不写数据库，也不发出站请求。

| 命令 | 说明 |
|---|---|
| `dtk --version` | 什么都不需要 |
| `dtk migrate --show` | 不连数据库，不需要 `DTK_SECRET_KEY` |
| `dtk backup list` | 只读文件系统 |
| `dtk user list` | |
| `dtk config list`、`dtk config get` | |
| `dtk identity list` | |
| `dtk proxy list` | |

### 安全，但有代价

这些对线上实例是安全的；只是你得知道自己花了什么。

| 命令 | 代价 |
|---|---|
| `dtk diagnose` | 真实出站请求：最多探测最早创建的 20 个代理（`MAX_PROXIES_PROBED`，所以更大的代理池只被覆盖了一部分），browser-rpc 为影子比对每个平台签一次名，冒烟步骤会花掉一个身份的一次请求。不写任何东西。`--skip-smoke` 可以去掉平台请求 |
| `dtk identity test` | 一次调度器之外的真实平台请求。不记录任何东西 |
| `dtk fetch` | 一次调度器之外的真实平台请求，外加 `archive.enabled` 打开时的一行归档 |
| `dtk proxy test` | 一次出站请求；除非 `--no-write`，否则会写回健康状态和地理信息 |
| `dtk backup create` | 读取所有导出表并 gzip。归档大时是几分钟 CPU，可能几 GB 磁盘 |
| `dtk proxy add`、`dtk proxy import` | 新增行，而且立刻生效：短链展开会从整张表里随机挑一个*健康*的代理，而新加的行在被探测之前就算健康。反过来也一样——只要表里有代理行而没有一个是健康的，展开就会失败而不是走直连，所以一张全是死代理的表会让短链解析坏掉。在这个代理上铸造出身份之前，没有任何东西会*用它签名*发请求 |
| `dtk config set` | 立即在所有运行中的进程上生效。这是正确的行为，也因此能瞬间改变系统表现 |
| `dtk identity mint` | 每铸造一个身份就驱动一次整个栈里最重的容器 |

### 救援命令——只在有意为之时使用

| 命令 | 为什么在这里 |
|---|---|
| `dtk user passwd` | 没人能登录时回到系统里的办法。Redis 挂了也能用 |
| `dtk user create` | 在初始化流程之外，从 shell 里创建管理员 |
| `dtk migrate` | schema 变更。正常是 `migrate` 容器的活；手动执行用于挑时间的升级或从备份恢复的数据库 |
| `dtk backup restore` | 重建实例。需要原始的 `DTK_SECRET_KEY`；它不是回滚 |
| `dtk identity retire` | 不可逆。会清空 Cookie 罐 |
| `dtk serve` | 手动启动一个 API。缺少容器 entrypoint 加的那些加固参数 |
| `dtk worker` | 手动启动一个 worker。真要扩容请用 `--scale worker=N` |

唯一要彻底避免的一件事：对一个你还没读过 manifest 的线上实例执行 `dtk backup restore -y`。那个确认提示存在的意义，就是让 manifest 告诉你即将合并进来的是什么；`-y` 是给已经知道答案的脚本用的。

## 接下来读什么

- [运维](./10-operations.md) —— 备份、升级、监控以及该盯着什么
- [故障排查](./14-troubleshooting.md) —— 如何读一份 `dtk diagnose` 报告，以及每种失败意味着什么
- [配置参考](./03-configuration.md) —— `dtk config` 能读写的每一项配置
- [身份与代理](./06-identities-and-proxies.md) —— 身份池在做什么，以及为什么
- [用户与 API 密钥](./09-users-and-api-keys.md) —— 角色、权限范围以及账号的控制台一侧
- [安全](./15-security.md) —— `DTK_SECRET_KEY` 保护的是什么，换掉它会发生什么
