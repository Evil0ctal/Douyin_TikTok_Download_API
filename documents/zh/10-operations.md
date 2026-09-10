# 运维

这一页讲的是上线之后的日子：怎样让一个实例在几个月里不用人盯着也保持健康。读完之后你会知道后台循环在做什么、改一个设置多久才会生效、怎么做备份和恢复（以及怎么证明恢复真的有效）、告警是怎么接的、哪些数据会在什么时候被清理、该盯哪些指标，以及怎样升级才不会丢东西。

本页默认你用的是[安装与部署](./02-installation.md)里的 compose 部署方式。所有命令都在仓库根目录执行，并且始终带上 compose 项目名：

```bash
docker compose -p dtk -f docker/compose.yml ps
```

## 后台都在跑什么

绝大部分「第二天以后」的行为都来自 `worker` 容器里的四个循环。它们是一个无人值守的实例还能继续可用的原因，也是它不可用时第一个该看的地方。

| 循环 | 间隔 | 一次 tick 做什么 |
| --- | --- | --- |
| `pool_filler` | 60s | 按平台统计身份数量。可用数低于 `pool.min_size` 时开始铸造，直到达到 `pool.target_size`。触发 `pool_empty` / `pool_below_min`。 |
| `proxy_prober` | 300s | 探测每一条代理记录，回写健康状态和出口地址，把失败代理背后的身份冷却 900 秒。触发 `proxy_unhealthy`。 |
| `maintenance` | 300s | 九个子任务，见下表。 |
| `watchlist` | 60s | 把到期的关注条目排入队列，每次最多 `watchlist.batch_size` 条；每 6 小时排一次存活性复查。 |

四个循环共用同一套实现（`src/dtk/worker/loop.py`），有三点值得知道：

- **抛出的异常永远不会终止循环。** 它会以 `worker.loop.failed` 记录，并带上 `consecutive_failures` 计数，下一次 tick 照常执行。
- **失败会退避。** 间隔按 `2^失败次数` 放大，上限是配置间隔的 8 倍。依赖挂掉的任务不会一直猛敲它，但也永远不会放弃。恢复时记录 `worker.loop.recovered`。
- **tick 带 ±10% 抖动**，这样多个 worker 副本不会在同一瞬间触发同一个任务。

一次 `maintenance` tick 会按顺序执行下面这些子任务，其中任何一个失败都不会影响其他：

| 子任务 | 做什么 |
| --- | --- |
| `apply_retention` | 重新应用保留窗口、清空过期任务的负载、删除过期任务行、应用快照压缩策略。 |
| `purge_retired_identities` | 删除退休时间超过 `retention.retired_identity_days` 的身份记录。 |
| `requeue_stale_tasks` | 把 `running` 状态超过 900 秒的任务放回队列（每轮最多 200 条）。 |
| `requeue_orphaned_tasks` | 重新投递那些 `queued` 超过 300 秒、但 Redis 队列上已经没有条目的任务。 |
| `refresh_aggregates` | 刷新没有刷新策略的 TimescaleDB 连续聚合，窗口为最近一天。 |
| `warn_expiring_sessions` | 对 3 天内即将过期的、导入的已登录身份触发 `cookie_expiring`。 |
| `check_capacity` | 测量磁盘，触发 `capacity_warning` / `capacity_paused`。不删除任何东西。 |
| `enforce_media_ceiling` | 超出 `media.max_bytes` 时按时间从旧到新清理已存媒体，已置顶的永不清理。触发 `media_evicted`。 |
| `fail_stale_downloads` | 结算那些 7200 秒后仍声称在传输中的下载。不动磁盘上的文件。 |

整轮结束会记录一条带各项计数的 `worker.maintenance.done`。只要这条日志每五分钟出现一次，实例的后台那一半就是活的。

## 设置页，以及一次修改何时生效

运行时配置存在数据库里，不在 `.env` 里。控制台页面是 `/settings`，按 key 前缀分组：signing、cache、snapshot、archive、media、watchlist、capacity、retention、api、security、notify、system，前缀不在列表里的归到 `other`。调度器和身份池相关的 key（`sched.*`、`pool.*`）刻意不在这里——它们在 `/scheduler` 页面，和它们作用的那个池放在一起。

每一行都会标明**来源**，这是这个页面上最有用的一件事：

| 来源标签 | 含义 |
| --- | --- |
| Database override（数据库覆盖） | `settings` 表里有这一行。它优先生效，`.env` 里的同名项会被忽略，直到你重置这一行。 |
| .env seed（.env 种子值） | 数据库里没有这一行，而环境里存在对应的 `DTK_*` 变量。 |
| Code default（代码默认值） | 数据库和环境都没有设置这个 key。 |

中间那一行有个坑。环境变量只会被复制进 settings 表**一次**，就是创建第一个管理员账号的时候（`POST /api/setup/init`）。此后配置加载只读 settings 表和内置默认值，别的都不读。所以在一个已经初始化过的实例上往 `.env` 里加 `DTK_CACHE_CONTENT_TTL=3600` 再重启，这一行会被标成 `.env seed`，但真正生效的仍然是代码默认值。在运行中的实例上，请在控制台或用 `dtk config set` 修改运行时设置——不要去改 `.env`。

**一次修改怎么传播。** 写入一个设置会先做校验和类型转换，然后存库、把 `settings_version` 计数器加一，并在 Redis 频道 `config:changed` 上发布通知。每个进程（API、worker、MCP）都订阅这个频道，*同时*每 30 秒轮询一次版本号计数器，因为 pub/sub 在重连过程中可能丢消息。于是：

- 处理这次写入的 API 进程立即重载；
- 其他进程在收到消息时更新，最迟 30 秒内也会更新；
- 快照是整体替换而不是逐字段修改，因此已经在处理中的请求会一直用它开始时的那份值。

**敏感 key**（`security.url_allowlist`、`security.cors_allow_origins`、`security.cors_allow_credentials`、`security.enable_task_webhook`、`security.webhook_secret`、`security.request_proxy`、`api.public_endpoints`）需要管理员角色和一次显式确认——控制台会要求你手动输入 key 名——并且会留下带账号和旧值的审计记录。每个 key 具体放宽了什么，见[安全](./15-security.md)。

**哪些修改必须重启 worker 才生效。** 有几个值在 worker 进程构建对象时就被固化了：

| 设置 | 被固化进 |
| --- | --- |
| `sched.max_wait_seconds` | 调度器 |
| `pool.health_prior` | 调度器 |
| `sched.circuit_risk_threshold`、`sched.circuit_min_samples`、`sched.circuit_min_identities` | 熔断器 |
| `sched.cooldown_base_seconds`、`sched.cooldown_max_seconds` | fetch 服务 |

worker 用到的其他配置——`notify.*`（含渠道列表）、`signing.*`、`retention.*`、`pool.min_size` / `target_size` / `max_fail_streak`、`capacity.*`、`media.*`、`watchlist.*`、`archive.*`——都是实时读取的。改动上面这四组之后：

```bash
docker compose -p dtk -f docker/compose.yml restart worker
```

引导层设置（`DTK_SECRET_KEY`、`DTK_DATABASE_URL`、`DTK_REDIS_URL`、`DTK_LOG_LEVEL`、`DTK_LOG_JSON`、`DTK_BROWSER_RPC_URL`、`DTK_DOWNLOADER_URL`、`DTK_BACKUP_DIR` 等）只能来自环境变量，改完必须重启容器。完整清单见[配置参考](./03-configuration.md)。

同样这三件事在终端里：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed
docker compose -p dtk -f docker/compose.yml exec api dtk config get retention.request_log_days
docker compose -p dtk -f docker/compose.yml exec api dtk config set retention.request_log_days 30
```

`dtk config set` 会在控制台用的同一个 Redis 频道上广播这次变更，所以运行中的部署无需重启即可生效。

## 一个备份里有什么，没有什么

归档是一个 gzip 压缩的 tar 包，里面有 `manifest.json`，以及 `data/` 下每张表一个 JSON Lines 文件，文件权限为 `0600`。

| 表 | 是否包含 | 原因 |
| --- | --- | --- |
| `users` | 总是 | 控制台账号，含密码哈希。 |
| `api_keys` | 总是 | 密钥记录（哈希、权限范围、限额）。 |
| `proxies` | 总是 | 加密后的代理 URL。 |
| `settings` | 总是 | 你配置过的一切。 |
| `content_snapshots` | 总是 | 几个月积累的历史数据，也是唯一无法重新抓回来的东西。 |
| `identities` | 仅 `--include-identities` | 绑定了某个代理和某个出口地址的 Cookie 罐。 |
| `request_log` | 从不 | 已经结束的那次运行的操作历史。 |
| `identity_events` | 从不 | 同上。 |
| `tasks` | 从不 | 还在执行中的工作。 |
| `audit_log` | 从不 | 针对「即将被替换掉的那个实例」的操作记录。 |
| `settings_version` | 从不 | 给运行中进程用的变更计数器，不是用户数据。 |

有两个特性决定了归档必须怎么处理：

**凭据是以密文导出的，归档里永远没有主密钥。** 代理 URL 和 Cookie 罐是从 `bytea` 列里逐字节拷出来的，导出过程不做解密。`settings` 里的凭据字段——Telegram bot token、钉钉签名密钥、SMTP 密码——会在导出时加密，这正是归档格式版本 2 新增的部分。manifest 里带的是派生密钥的 HMAC 指纹，不是密钥本身：足以判断「这是另一把钥匙」，不足以反推出它。**恢复必须使用同一个 `DTK_SECRET_KEY`。** 丢了这把钥匙，所有归档都无法解密；请把它备份在归档之外的地方。

**身份默认不导出**，这是正确的语义，不是偷懒。一个身份是绑定在一个代理和一个出口 IP 上的 Cookie 罐。迁移之后出口变了，用新的出口地址重放这些 cookie，恰恰是最容易触发风控的做法。只有在把整套部署迁到出口相同的机器上时才需要带上它们。你在控制台打开这个开关时，会看到同样意思的警告。

由于归档里仍然带着 settings，而 `notify.channels` 里有 webhook URL 和 token，**归档本身就是一个凭据文件**，请按凭据来保管。

## 创建备份

**没有任何东西会定时创建备份。** 系统里没有周期性的备份任务，定时是你自己的事。告警也不是每条路径都有：`backup_failed` 只在 worker 执行的备份失败时触发——也就是控制台按钮和 `POST /api/v1/admin/backup`。`dtk backup create` 失败不会发任何告警，它只是打印原因并以非零码退出，所以 cron 里的备份得自己处理失败——检查退出码，或者盯着 `dtk backup list`。

在控制台里：`/backup` → **创建备份**，仅管理员可用。请求会作为任务排队，由 worker 写入备份目录；页面会轮询，新归档随后出现在表格里。**包含身份** 开关默认关闭，打开时会显示警告。

用 API——密钥必须属于管理员账号，并且带有 `admin` 权限范围：

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/admin/backup \
  -H "X-API-Key: $DTK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"include_identities": false}'
```

它返回 `202` 和一个 `task_id`；轮询 `/api/v1/tasks/{task_id}` 可拿到文件名、大小和行数。任务轮询的用法见 [REST API 指南](./11-api.md)。

用终端——这条路在 worker 挂掉时仍然可用，因为 CLI 是在自己的进程里完成导出的：

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
```

在容器里 `-o` 不是可选的。`dtk backup create` 默认写到相对于工作目录的 `backups`，而容器文件系统除了备份卷和 `/tmp` 之外都是只读的，所以走默认值会失败。`/var/lib/dtk/backups` 是命名卷 `backup-data`，同时挂载给 `api` 和 `worker`——API 负责列出并提供 worker 写下的归档，所以两者必须指向同一个目录。在容器之外默认值没问题：在仓库检出目录里执行 `dtk backup create` 会写进 `./backups`。

归档命名为 `dtk-backup-<UTC 时间戳>.tar.gz`，例如 `dtk-backup-20260910T031500Z.tar.gz`。导出过程会把各表暂存在归档旁边的隐藏目录里，写完后再原子重命名，所以读取方永远不会看到写了一半的 `.tar.gz`。

宿主机上每天一次、保留 14 天的 cron：

```bash
15 3 * * * cd /srv/dtk && docker compose -p dtk -f docker/compose.yml exec -T api \
  dtk backup create -o /var/lib/dtk/backups >> /var/log/dtk-backup.log 2>&1
20 3 * * * docker run --rm -v dtk_backup-data:/b alpine \
  sh -c 'find /b -name "dtk-backup-*.tar.gz" -mtime +14 -delete'
```

上面那条 cron 里的 `-T` 不是摆设：cron 没有终端，而 `docker compose exec` 不带 `-T` 时会去分配一个 TTY，没有 TTY 就会失败。本页其余命令都是在终端里手敲的，所以用的是不带 `-T` 的 `exec`；你要写脚本或接管道时，再把 `-T` 加回去。

如果 worker 执行的备份失败——磁盘满、文件系统只读、导出中途数据库断开——会触发 `backup_failed` 告警（每 24 小时一次），具体原因写在 worker 日志里。上面那个 cron 任务对应的信号则是退出码。无论哪种，它都是你和「在恢复时才发现问题」之间唯一的东西。

## 列出归档与读取 manifest

`/backup` 页面的表格每个归档一行：文件名、创建时间、写它的版本与归档格式版本、是否包含身份、行数、大小，还有一列 **密钥校验**（默认隐藏，可在列菜单里打开），显示 `当前密钥` 或 `其他密钥`。这一列是服务端算出来的一个比特——manifest 里的密钥指纹永远不离开 API 进程，因为把它发出去等于让人可以离线验证对 `DTK_SECRET_KEY` 的猜测。

解析不了的归档也会列出来，并附上原因。这是刻意的：损坏的归档恰恰是这个页面最需要能显示出来的东西，因为那正是有人打开它的理由。

在终端里：

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
```

列是 file、created、version、rows、identities、size，再加一列 `state`（`ok` 或 `error`）。和控制台不同的是，它既不打印归档 schema 版本，也不显示密钥是否匹配——这两项要去 `/backup` 看对应那一行。和 `create` 一样，`--dir` 默认是相对工作目录的 `backups`，在容器里要显式传。

## 恢复归档

在写入任何一行之前会检查三件事，任何一项不通过都会明确告诉你是哪一项：

1. 文件存在且是可读的归档；
2. 它的 `schema_version` 是本版本能读的（目前是 1 或 2，当前写入方产出 2）；
3. manifest 里的密钥指纹与本实例的 `DTK_SECRET_KEY` 匹配。

**恢复是补齐，不是覆盖。** 插入使用 `ON CONFLICT DO NOTHING`，顺序为 `users → api_keys → proxies → identities → settings → content_snapshots`，以便外键能解析。已存在的行优先保留，因此往一个正在使用的实例里恢复，是补上缺失的部分，而不是把现状回退掉——同一个归档恢复两次也无害。`content_snapshots` 是超表、没有唯一索引，所以那张表的重复行是按 `(ts, platform, content_type, content_id)` 显式过滤掉的。返回的每表计数是归档*提供*了多少行，而不是新写入了多少行；数据库不报告这个差值，编一个数字比如实说明读了多少更糟。

要注意由此带来的后果：恢复无法把实例回滚。如果你要的是归档时刻的确切状态，请恢复到一个空数据库里。

在控制台里：`/backup` → 某一行的 **恢复**。仅管理员，且必须手动输入文件名确认。恢复会作为 worker 任务排队，所以这条路需要 worker 是健康的。完成后请刷新控制台，让每个页面重新读取数据。

在终端里——在自己的进程里执行，所以这是故障期间该走的路：

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/dtk-backup-20260910T031500Z.tar.gz
```

它会先打印 manifest，请求确认，然后打印每表计数。脚本里可以加 `--yes` 跳过确认。如果密钥不匹配，你会得到一条明确的提示，并且什么都不会被写入。

当归档里包含 `settings` 时，恢复会把 `settings_version` 加一，这样每个运行中的进程都会重载配置，而不是一直用恢复前的快照直到下次重启。

## 一次恢复演练

没验证过的备份只是一个假设。每季度演练一次，十分钟就够，而这是唯一能在代价还很低的时候，发现你的 `DTK_SECRET_KEY` 并不是你以为的那把的方法。

演练用同一份检出跑第二套隔离的栈：换一个 compose 项目名就会得到独立的卷，换一个发布端口就不会和正在运行的实例冲突，而共用的 `.env` 意味着演练实例持有同一个 `DTK_SECRET_KEY`——这正是归档能被恢复的前提。

```bash
# 1. 取一份新归档，并从卷里拷出来。
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml cp \
  api:/var/lib/dtk/backups/dtk-backup-20260910T031500Z.tar.gz ./drill.tar.gz

# 2. 在旁边用另一个端口起一套空栈。
DTK_BIND_PORT=8010 docker compose -p dtk-drill -f docker/compose.yml up -d --wait

# 3. 恢复进去。
docker compose -p dtk-drill -f docker/compose.yml cp ./drill.tar.gz \
  api:/var/lib/dtk/backups/drill.tar.gz
docker compose -p dtk-drill -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/drill.tar.gz --yes

# 4. 验证。
curl -fsS http://127.0.0.1:8010/readyz
```

然后打开 `http://127.0.0.1:8010`，检查三件事：

- **能用归档里的账号登录。** 一个新实例只有在没有任何用户行时才算「未初始化」；恢复 `users` 会关闭初始化窗口，所以归档里的管理员密码就是能用的那个。
- **设置回来了**——`/settings` 上应该看到你的覆盖值，而不是默认值。
- **历史回来了**——「总览」的图表和「资料库」里应该有你预期的快照。

关于演练栈本身还有两点。它没有身份（除非你演练的是带 `--include-identities` 的归档），所以不会产生任何平台流量；但它确实有你的代理，所以它的代理探测器会每五分钟拨一次你的每个代理，直到你把它停掉。用完立刻连卷一起拆掉：

```bash
docker compose -p dtk-drill -f docker/compose.yml down -v --remove-orphans
rm drill.tar.gz
```

## 告警渠道

只在控制台里显示的告警不算告警：凌晨三点池子空了的时候没人在看控制台。渠道在 `/notifications` 页面配置，这个页面本质上是三个运行时设置——`notify.enabled`、`notify.language`、`notify.channels`——的带类型编辑器，而不是第二份事实来源。

`notify.enabled` **默认是关的**。关着的时候触发条件照样触发、照样记日志，只是什么都不发出去。

支持六种渠道类型。每个渠道都带一个 `name`（用于测试和审计记录）、一个可选的 `language`（告警按接收者的语言渲染，而不是按控制台操作者的语言）、一个 `enabled` 开关，以及一个可选的事件子集。

| 类型 | 必填 | 可选 | 说明 |
| --- | --- | --- | --- |
| `webhook` | `url` | — | 通用 JSON POST；payload 结构由本项目定义，是稳定的。 |
| `bark` | `url` | `group` | 严重级别映射到 Bark 的 `level`。 |
| `wecom` | `url` | — | 企业微信群机器人。 |
| `dingtalk` | `url` | `secret` | 填了密钥就会在每次请求时对 URL 签名。 |
| `telegram` | `token`、`chat_id` | — | URL 由 `https://api.telegram.org/bot<token>/sendMessage` 拼出。 |
| `smtp` | `host`、`sender`、`recipients` | `port`（587）、`username`、`password`、`ssl`、`starttls` | 发送是阻塞式 SMTP，放在事件循环之外执行。 |

通用 webhook 的 payload：

```json
{
  "source": "dtk",
  "event": "pool_empty",
  "severity": "error",
  "title": "Identity pool empty",
  "body": "No active identity is left for douyin. Requests are queued or rejected until the pool recovers.",
  "language": "en",
  "sent_at": "2026-09-10T03:15:00+00:00",
  "details": { "platform": "douyin" }
}
```

目标地址是在保存渠道时校验的，不是在告警触发时才校验，这样填错的目标会在你还看着表单的时候就被拒绝。规则很严格，家庭环境下尤其容易踩：

- **只允许 https。** 明文 http 会把告警正文和 URL 里的 token 明文发到网络上。
- **authority 里不允许带凭据**（`https://user:pass@host/...` 会被拒绝）。
- **不允许私有地址、回环地址和链路本地地址**——SMTP 的 `host` 同样如此。局域网里的 webhook 接收端或邮件服务器不能作为渠道目标。非标准端口没问题；决定一个地址是不是内网地址的不是端口。

投递被刻意做得很便宜：8 秒超时，最多 2 次尝试、间隔 1 秒退避，且只对 429、500、502、503、504 重试。重试太狠的告警系统，自己就会变成它要报告的那场故障。失败原因在记入日志或存库前会被抹掉凭据——Telegram 的 token 就在 URL 路径里，任何引用了该 URL 的异常文本都是凭据。

已保存的凭据在读取时以掩码返回，在写入时会被合并回去。编辑器里显示为 `***` 的字段，如果你不动它就保持原值；输入新值即替换，清空即删除。如果你重命名了渠道或把它指向了另一个主机，原来的凭据**不会**被沿用——你会被要求重新输入，这是刻意设计。

一个配错的渠道不会让其他渠道跟着哑掉：`build_channels` 会跳过校验不通过的条目、记录 `ops.notify.channel_rejected`，然后照常投递给其余渠道。

## 触发条件与去重窗口

事件集合是固定的。去重是功能而不是优化：没有窗口的话，一个熔断的接口会每个请求发一条通知，这样十分钟之后你就会把告警彻底关掉。每个事件都有一个窗口和一个作用域，窗口内同一作用域的第二条告警会被抑制。这些窗口不允许按部署调整——正是它们让告警值得信任。

| 事件 | 级别 | 窗口 | 作用域 | 何时触发 |
| --- | --- | --- | --- | --- |
| `endpoint_circuit_open` | error | 30 分钟 | endpoint | 某个接口熔断，开始拒绝请求。 |
| `pool_empty` | error | 15 分钟 | platform | 某个平台已经没有 active 身份。 |
| `pool_below_min` | warning | 60 分钟 | platform | active 身份数低于 `pool.min_size`。 |
| `proxy_unhealthy` | warning | 60 分钟 | proxy | 某个代理探测失败，被移出轮转。 |
| `signature_stale` | error | 24 小时 | endpoint | 进程内签名与浏览器结果不一致。 |
| `cookie_expiring` | warning | 24 小时 | identity | 某个导入的已登录身份将在 3 天内过期。 |
| `backup_failed` | error | 24 小时 | 实例 | 某次备份没有完成。 |
| `capacity_warning` | warning | 6 小时 | 卷 | 磁盘占用超过 `capacity.warn_percent`。 |
| `capacity_paused` | error | 1 小时 | 卷 | 磁盘占用超过 `capacity.hard_stop_percent`，后台写入方已停下。 |
| `media_evicted` | warning | 6 小时 | 实例 | 为了不超过 `media.max_bytes`，已删除部分存储媒体。 |

只有真正送达了某个人的告警才会占住窗口。如果所有渠道都拒绝了这条告警，标记会被释放，下一次发生时会重新尝试——否则一次被拒的连接就能换来长达一天的沉默。

**给渠道缩小事件范围有个尖锐的坑。** 默认情况下一个渠道接收所有事件，这是通过*不写*事件列表来表示的。控制台的勾选框只列出上表中的前七个事件，三个磁盘相关事件（`capacity_warning`、`capacity_paused`、`media_evicted`）不在其中。所以你只要取消勾选任意一项，这个渠道就会被存成一个显式列表，而这个列表里不可能包含那三个磁盘事件，于是它就再也收不到它们了。如果你要磁盘告警，请至少让一个渠道保持全部勾选。

## 测试渠道

`/notifications` 页头有**发送测试告警**（所有渠道），每一行还有一个**测试**按钮（该渠道）。需要 operator 及以上角色。

测试完全不走去重窗口——既不占用也不读取——所以按两次就会发两次，而且用测试验证渠道不会消耗真实告警的抑制窗口。测试正文里会写明发到了哪个渠道，这样你拿着两部手机也能分清是哪一个。

结果要仔细看：任务完成不等于投递成功。控制台会区分三种结果——已发送、被拒绝（带渠道名和原因）、以及「告警总开关是关的，所以什么都没发」。最后一种意味着 `notify.enabled` 是 `false`；这时照发不误只会让你相信一条其实不会被使用的通道是通的。

每次改完 `notify.channels` 都点一下测试，这个两秒钟的习惯很值，因为另一种选择是在真正需要这条告警的那场故障里发现自己打错了字。

## 数据保留：清理什么、什么时候清理

| 设置 | 默认值 | 约束什么 | 由谁执行 |
| --- | --- | --- | --- |
| `retention.request_log_days` | 14 | `request_log` 行 | TimescaleDB 保留策略 |
| `retention.identity_events_days` | 90 | `identity_events` 行 | TimescaleDB 保留策略 |
| `retention.task_result_hours` | 24 | 已完成任务的 `result`/`error` 负载 | 维护轮次里的 `UPDATE` |
| `retention.task_days` | 90 | 已完成（`done`/`failed`）的任务行 | 维护轮次里的 `DELETE` |
| `retention.retired_identity_days` | 90 | 已退休身份的行 | 维护轮次里的 `DELETE` |
| `retention.content_days` | 0 | 目前什么都不约束——没有任何代码读这个 key | — |

保留窗口的取值范围校验为 1 到 3650 天。超出范围的值，或者不属于那两张超表的表名，会被单独报告出来，而维护轮次的其余部分照常执行——不能因为一个数字填错就连任务负载都不清了。

一些值得注意的细节：

- **两张超表的窗口由 TimescaleDB 执行，不是由 dtk 执行。** 修改窗口时会先删掉现有策略、再用新间隔加一遍，因为 TimescaleDB 没有「改间隔」这个调用，而带 `if_not_exists` 的 `add_retention_policy` 会静默保留旧窗口。重新应用发生在下一次维护轮次，也就是五分钟以内。真正的分块删除随后由 TimescaleDB 自己的后台调度执行。如果 Postgres 上没装 TimescaleDB 扩展，这些策略调用会失败，报告里带上 `the retention policy could not be applied; is TimescaleDB installed?`，这两张表就没有任何东西在清理。
- **清空任务负载不等于删除任务。** 行还在，统计因此还在；之后再去取那个任务的结果会得到 `TASK_NOT_FOUND`。
- **`content_snapshots` 永远不会有保留策略。** 那是你积累下来的数据，平台一旦下架就再也抓不回来，删除它必须是一个显式动作。自动执行的只有压缩：超过 7 天的分块会转入列存，让它更便宜，但不会更难读。
- **`retention.content_days` 是空转的。** 它在设置注册表里有声明，也会出现在 `/settings` 上，但没有任何代码读它，所以无论填什么，行为都等同于默认的「永不删除」。要删除归档作品，请用[资料库](./08-downloads-and-library.md)里的删除操作。
- **`audit_log` 不会被任何东西清理。** 这是刻意的——见[安全](./15-security.md)——但也意味着这张表会伴随实例一直增长。

## 容量护栏

护栏不是保留策略：这里什么都不删。它测量文件系统、发出警告，并在超过阈值后停掉那些没人在等的写入方。

| 设置 | 默认值 | 效果 |
| --- | --- | --- |
| `capacity.warn_percent` | 80 | 触发 `capacity_warning`。不改变任何行为。 |
| `capacity.hard_stop_percent` | 92 | 触发 `capacity_paused`。定时采集停下，`POST /api/v1/downloads` 拒绝新任务并给出 300 秒的重试提示。 |

两者都是 1 到 99 之间的百分比，写入时校验——0 会让实例一启动就暂停，150 则意味着护栏永远不会触发。

占用率通过对 `/var/lib/dtk`（数据库卷）和 `/var/lib/dtk/media`（媒体卷）执行 `disk_usage` 得到，以容器自己看到的为准，并按设备号去重，这样同一个文件系统挂两次不会被算两遍。判定看的是*最满的*那个卷，不是平均值：先满的那块盘才是把东西弄坏的那块。不存在的路径会被跳过——没启用 downloader profile 的实例本来就没有媒体卷，这不是故障。

交互式 API 读取永远不会被暂停。把磁盘满变成「我的 API 挂了」，比要预防的问题更糟；为了腾空间去删用户的归档，比这两者都糟。

媒体上限是另一套机制，策略正好相反——它**确实会删**：

| 设置 | 默认值 | 效果 |
| --- | --- | --- |
| `media.max_bytes` | 2 GiB | 超出后从最旧的、未置顶的下载开始删除文件，直到卷回到上限以下。填 `0` 完全关闭清理。 |
| `media.max_file_bytes` | 512 MiB | 单个文件上限。按实际写入的字节数计，而不是按服务器声称的大小。达到上限的传输会被拒绝，且不留残留文件。 |

清理的总量取自卷本身而不是数据库，永远跳过已置顶的下载，只删文件（记录、摘要和文件清单都保留，所以「资料库」仍然显示采集过什么，并标明它已被清理），并触发 `media_evicted`——这是你唯一会收到的、关于「你的磁盘策略刚刚执行过」的通知。

## 监控

有两个无需认证的探针和三个需要认证的视图。监控系统对准探针；出问题时再看视图。

| 信号 | 位置 | 健康的样子 |
| --- | --- | --- |
| 存活 | `GET /healthz` | `200`，`{"status":"ok","uptime_seconds":…}`。刻意不碰任何依赖：一个会查数据库的存活探针，会把数据库的短暂抖动变成一场重启风暴。 |
| 就绪 | `GET /readyz` | `200`，且 `postgres` 与 `redis` 均为 `ok`；否则 `503`。浏览器 RPC 被刻意排除在外——铸造不在请求路径上，没有它的实例是降级，不是未就绪。 |
| 实例状态 | `GET /api/v1/system/status`、控制台 `/system` | 各组件可达且延迟正常；Chromium 主版本与 wreq profile 主版本一致；`settings_version` 与预期相符。 |
| 接口健康 | `GET /api/v1/admin/endpoints/health`、控制台「总览」 | 所有接口 `circuit_open: false`；风控率远低于 `sched.circuit_risk_threshold`（0.6）。 |
| 流量构成 | `GET /api/v1/admin/metrics/timeseries`、控制台「总览」 | 绝大多数是 `ok`；`risk_control` 很少见，且不集中在某一个接口上。 |
| 池水位 | 控制台 `/identities`、`/system` | 你在用的每个平台，active 身份数不低于 `pool.min_size`（3）。 |
| 磁盘 | 控制台 `/system`、`capacity_*` 告警 | 低于 `capacity.warn_percent`。 |
| 后台循环 | `worker` 日志 | 每约 5 分钟一条 `worker.maintenance.done`；没有反复出现的 `worker.loop.failed`。 |
| 备份 | 控制台 `/backup` | 存在一份在你的备份周期内的归档，且密钥校验显示 `当前密钥`。 |

容器层面由 compose 自己的健康检查覆盖：postgres 用 `pg_isready`，redis 用带认证的 `redis-cli ping`，api 用 `GET /readyz`，worker 用一个 Redis TCP 探测（它自己没有端口），browser-rpc 用 `GET /rpc/health`——最后这个会去读响应体里的 `status` 字段而不是满足于 200，因为即使浏览器后端启动失败，该服务也会回应这个探针。

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
docker compose -p dtk -f docker/compose.yml ps
```

控制台的「日志」页面（`/logs`）有两个标签页：请求日志，可按 request id、接口、结果和时间范围检索；以及审计记录——谁改了什么，单独存表，这样它永远不会被普通流量淹没。设置变更、备份与恢复请求、告警测试和自检都会留下审计记录。

想做一次覆盖出网、代理、签名和端到端请求的结构化自检，可以运行诊断——在控制台 `/diagnose`，或者：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

它的报告在生成时就做了脱敏，可以放心贴进 issue。怎么读这份报告见[故障排查](./14-troubleshooting.md)。

## 安全地升级

`migrate` 是一个一次性容器，在 `api` 和 `worker` 启动之前运行，而 Alembic 是幂等的，所以迁移不需要你记着做。命名卷（`postgres-data`、`redis-data`、`backup-data`、`media-data`）在重建镜像后依然存在。

```bash
# 1. 先备份，并确认它已经被列出来。
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups

# 2. 拉取、重建、重启。
git pull
docker compose -p dtk -f docker/compose.yml build
docker compose -p dtk -f docker/compose.yml up -d --wait

# 3. 确认。
curl -fsS http://127.0.0.1:8000/readyz
docker compose -p dtk -f docker/compose.yml logs --tail 50 migrate
```

`up -d --wait` 会在健康检查通过后返回，而 `api` 的健康检查用的是就绪探针，所以正常返回意味着 API 真的能对外服务了。如果 migrate 容器以非零码退出，`api` 和 `worker` 根本不会启动——这正是 `service_completed_successfully` 在起作用，原因写在 migrate 的日志里。

升级之前值得知道的几点：

- **不要改 `DTK_SECRET_KEY`。** 改了它，所有已存的 Cookie 罐和代理 URL 都无法解密，所有现有归档也都无法恢复。如果必须轮换，就退休受影响的身份，然后重新导入或重新铸造。
- **回滚靠 tag，不靠数据库。** 迁移只向前走，本文不提供降级路径。如果某个版本有问题，检出上一个 tag、重建，然后用第 1 步的备份恢复——恢复到空数据库里，因为恢复是补齐而不是回退。
- **`build` 和 `up` 只作用于当前激活 profile 里的服务。** 如果你的部署带了 `--profile browser` 或 `--profile downloader`，这两条命令都要把 profile 参数重复带上。compose 读的是文件而不是正在运行的栈，不带参数时没有任何东西会重建 `browser-rpc` 和 `downloader`：它们会继续跑旧镜像，而 `api` 和 `worker` 已经往前走了，并且不会有任何提示。完整的 profile 参数见[安装与部署](./02-installation.md)。
- **浏览器镜像是刻意钉死的。** `CLOAKBROWSER_COMMIT` 是构建参数，和发布地址一样走 compose 插值：它从 `docker/.env` 或你的 shell 读取，而不是根目录的 `.env`。重建浏览器镜像时在命令行里传给它，或者用 `COMPOSE_ENV_FILES=.env` 让 compose 去读根目录那个文件。它精确指向某一个 revision；有好几个互不相关的仓库都发布着描述雷同的 "cloakbrowser" 项目，浮动 tag 并不能标识任何一份具体的软件。要改就有意识地改，改完重新验证签名。
- **升级后留意版本漂移**：`/system` 上产出签名的 Chromium 主版本，和重放指纹的 wreq TLS profile 主版本必须一致。
- **扩容靠加 worker，不靠改间隔**：`docker compose -p dtk -f docker/compose.yml up -d --scale worker=4`。

## 容量规划：身份数与 worker 数

吞吐由身份池决定，不由 worker 数量决定。这些数字都是可以读出来的，所以按数字规划，不要靠猜。

每个 `(identity, endpoint)` 组合有一个令牌桶，而每个身份**同一时刻只处理一个上游请求**，与接口无关。桶的参数来自 `src/dtk/scheduler/policies.py`：

| 接口 | 突发 | 补充速率 | 每身份 |
| --- | --- | --- | --- |
| `*.content_detail` | 5 | 0.30/s | 18 次/分钟 |
| `*.author_profile` | 4 | 0.20/s | 12 次/分钟 |
| `*.comments`、`*.comment_replies`、`*.mix_posts` | 3 | 0.15/s | 9 次/分钟 |
| `*.author_posts`、`*.author_likes`、`tiktok.author_followers`、`tiktok.author_following` | 3 | 0.12/s | 7.2 次/分钟 |
| `*.session_check` | 3 | 0.20/s | 12 次/分钟 |
| 未注册的接口 | 3 | 0.15/s | 9 次/分钟 |

于是：

- **稳态速率**（单接口）≈ *身份数 × 补充速率*。每分钟 60 次视频详情需要 60 ÷ 18 ≈ **4 个身份**满负荷运行，而你需要留余量：身份在被风控后会进入冷却，处于 `cooling` 或 `degraded` 的身份不干活。对这个速率来说 6 到 8 个是现实的池子大小，这也正是 `pool.target_size` 默认 8、`pool.min_size` 默认 3 的原因。
- **突发量**是 *身份数 × 桶深度*，之后就回落到稳态速率——4 个身份大约能先打 20 次详情，然后进入匀速。
- **并发**受可用身份数限制，因为一个正忙的身份上无法开始第二个请求。单接口并发被设计为 1，也应该保持为 1：真实会话不会并行发 API 调用。要扩吞吐就加身份，绝不要加并发。
- **worker**：每个 `worker` 进程同时跑 4 个任务。多出来的槽位只是在调度器那里排队，所以超过 ⌈身份数 ÷ 4⌉ 的 worker 除了排队什么都不会带来。池子在 8 以内时一个 worker 就够；先扩池子。
- **队列会主动卸载压力，而不是无限增长。** 一次提交在碰到身份之前就已经是一个任务了——API 先回 `202`——这个任务随后最多等 `sched.max_wait_seconds`（10 秒）拿一个空闲身份，等不到就以 `IDENTITY_POOL_EXHAUSTED` 失败，并带上 `Retry-After`。而当任务队列已经达到 `sched.queue_max`（500）条时，新的提交会在更早的地方就被拒掉，返回 `QUEUE_FULL` 和它自己的 `Retry-After`。这两种拒绝出现的位置不一样：`wait_timeout` 是调度器给出的拒绝原因，会写进 `request_log`，所以你在 `/logs` 上看得到；`queue_full` 是 API 进程在任务还不存在时就抛出的，永远不会进 `request_log`，只出现在调用方拿到的错误信封的 `details.reject_reason` 里。无论哪一种，都说明你缺的是身份，不是 worker。
- **API 侧的限流是另一回事。** `api.default_rate_limit_per_min`（120）是按凭据的滥用防护，不是吞吐规划。

扩池子就要同步扩代理：一个身份绑定一个出口，把一堆身份堆在同一个出口地址后面，正是让那个地址被盯上的做法。这一侧见[身份与代理](./06-identities-and-proxies.md)。

资源上限写在 `docker/compose.yml` 里，扩容前值得先读一遍：`api` 和 `worker` 各 512 MiB、2 CPU，postgres 2 GiB，`browser-rpc` 4 GiB、4 CPU（Chromium 在预热状态下确实能吃掉六个核的量）。调高 `DTK_BROWSER_WARM_CONTEXTS` 会让浏览器容器的 `/tmp` tmpfs 每个常驻页面多占约 300 MB、且按平台各算一份——请连同 tmpfs 一起调大，否则 Chromium 会以一种「看起来完全像平台封锁」的方式崩溃。

## 读日志

日志是结构化的、只用英文，输出到 stdout。`DTK_LOG_LEVEL`（默认 `info`）和 `DTK_LOG_JSON`（默认 `true`）属于引导层设置：在 `.env` 里改，然后重启容器。

```bash
docker compose -p dtk -f docker/compose.yml logs -f worker
docker compose -p dtk -f docker/compose.yml logs --tail 200 api
```

事件名是点分标识符，从来不是句子，这样 grep 在版本之间保持稳定，也才有可能按事件类型聚合：`worker.loop.started`、`worker.maintenance.done`、`worker.maintenance.capacity`、`worker.maintenance.media_evicted`、`ops.notify.dispatched`、`ops.notify.delivery_failed`、`ops.retention.policy_applied`、`ops.backup.created`、`config.updated`、`config.reloaded`、`system.probe_failed`。

`DTK_LOG_JSON=true` 时用 `jq` 过滤。记得加 `fromjson?`，这样 entrypoint 输出的纯文本启动行不会中断整条管道：

```bash
docker compose -p dtk -f docker/compose.yml logs --no-log-prefix --tail 2000 worker \
  | jq -R 'fromjson? | select(.event | startswith("worker.maintenance"))'

docker compose -p dtk -f docker/compose.yml logs --no-log-prefix --tail 2000 worker \
  | jq -R 'fromjson? | select(.level == "error")'
```

如果你要盯着实时日志读，把 `DTK_LOG_JSON` 设为 `false`：那会切换到带颜色的控制台渲染，对人眼友好得多，对日志聚合系统则毫无用处。

关于日志里*没有*什么，有两点：

- **脱敏发生在处理器链里，不在调用点。** 名为 `cookie`、`authorization`、`api_key`、`password`、`secret`、`token`、`proxy_url`、`dtk_secret_key` 之类的字段会被整体替换，签名与会话参数（`msToken`、`a_bogus`、`X-Bogus`、`_signature`、`sessionid`、`ttwid` 等）无论出现在哪个字符串里，都会被截断到 6 个字符。靠调用点自觉，正是本项目上一代泄漏过一个有效会话 cookie 的原因。
- **`httpx`、`httpcore` 和 `hpack` 被固定在 WARNING**，与你设的级别无关。`httpx` 会在 INFO 级别记录每一条请求行，URL 也在里面——这是泄漏而不只是噪音，因为回调 URL 里经常带 token，签名过的 CDN 链接里带签名。那些行说的一切，本项目自己的 `transport.request.done` 都有，且 URL 是做过掩码的。

逐请求的历史根本不在容器日志里：它是 `request_log` 表里每请求一行，「总览」的图表和「日志」页面读的都是它。熔断器读的不是它：熔断判定跑在它自己那个 300 秒的 Redis 滚动窗口（`sched:window:<endpoint>`）上，所以接口健康的那组数字和从日志算出来的图表回答的是不同的问题，两者不会永远一致。每行包含时间戳、`request_id`（与 API 响应信封里返回的是同一个）、任务 id、平台、接口、身份与代理、API 密钥、结果（`ok`、`business_error`、`risk_control`、`network_error`）、HTTP 状态码、耗时、是否命中缓存、由哪个签名器产出签名、错误码，以及调度器未发放租约时的拒绝原因。这一行由 `FetchService` 写在请求自己的数据库会话上，因此它和请求在同一个事务里提交，而不是被批量刷写；租约被拒时也会先写下这一行再抛错——正是这一点让 `/logs` 在你打开它想看的那场故障里仍然有内容。

## 日常运维节奏

| 频率 | 做什么 |
| --- | --- |
| 自动 | cron 定时备份；至少有一个接收全部事件的告警渠道。 |
| 每周 | 扫一眼 `/system`（版本漂移、磁盘）和 `/backup`（是否有周期内的归档，密钥校验是否为 `当前密钥`）。 |
| 每月 | 快速看一遍 `/logs` → 审计，找有没有你不认识的变更。在 `/identities` 上看身份退休是不是比平时快。 |
| 每季度 | 做一次上面的恢复演练。清理你已经认不出来的 API 密钥（[用户与 API 密钥](./09-users-and-api-keys.md)）。 |
| 每次升级前 | 先备份，读发布说明，升级，然后检查 `/readyz` 和 `/system`。 |
| 每次改完 `notify.channels` | 点一下**发送测试告警**。 |

## 相关页面

- [安装与部署](./02-installation.md) —— 本页所运维的 compose 栈、profile 和卷。
- [配置参考](./03-configuration.md) —— 每一个设置项、它的作用域和默认值。
- [控制台总览](./05-console-overview.md) —— 每个页面显示什么。
- [身份与代理](./06-identities-and-proxies.md) —— 怎么扩池子、怎么修池子。
- [下载、资料库与关注列表](./08-downloads-and-library.md) —— 容量护栏会暂停的那些写入方。
- [用户与 API 密钥](./09-users-and-api-keys.md) —— 角色，以及本页各项操作分别需要谁。
- [命令行参考](./13-cli.md) —— 本页用到的每一条 `dtk` 命令，以及它们完整的参数列表。
- [故障排查](./14-troubleshooting.md) —— 怎么读诊断报告，以及常见故障。
- [安全](./15-security.md) —— 敏感设置、审计记录和密钥保管。
