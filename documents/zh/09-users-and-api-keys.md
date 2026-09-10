# 用户与 API 密钥

这一篇讲的是"谁能在你的实例上做什么"。读完之后，你会知道该给同事开哪种控制台账号、怎么签发一把只能读抖音、别的什么都干不了的密钥、怎么让泄露的凭据立刻失效，以及如果你真的决定把某个端点开放给不带凭据的调用方，会发生什么。

## 两类凭据

到达 API 的每个请求都会被解析成一个 *principal*（调用主体）：账号 id、角色、一组 scope（本地化界面与错误消息里称"权限范围"），以及（如果用的是密钥）密钥 id。路由处理函数从不关心你是"怎么"认证的，只看解析出来的主体被允许做什么。产生主体的方式恰好只有两种，再加上一个匿名主体——它只在运维人员主动开放的端点上存在。

| | 控制台会话 | API 密钥 |
|---|---|---|
| 如何携带 | `dtk_session` cookie，由 `POST /api/v1/auth/login` 下发 | `X-API-Key: dtk_...` 或 `Authorization: Bearer dtk_...` |
| 受什么约束 | 账号的**角色** | 签发时写进密钥的 **scope**，*以及*所属账号的角色 |
| 面向谁 | 浏览器前面的人 | 脚本、定时任务、Agent |
| 能用于 REST API | 能 | 能 |
| 能用于 `/mcp` | 不能，会被明确拒绝 | 能，而且是唯一被接受的凭据 |
| 能修改密码 | 能 | 不能，返回 `INVALID_PARAM` |
| 存放在 | Redis，7 天 | Postgres，直到被撤销或过期 |
| 如何失效 | 登出，或管理员重置该账号密码 | `DELETE /api/v1/admin/api-keys/{key_id}` |

控制台会话**不**受 scope 限制：它携带完整的 scope 集合，改由账号角色来约束，因为控制台正是这些角色被设计出来面对的界面。API 密钥两头都受约束，而且 scope 这一半绝不会因为密钥属于管理员就放松——见[密钥无法绕过的规则](#密钥无法绕过的规则)。

对应的代码是 `src/dtk/api/deps.py`，篇幅不长，想弄清确切的解析顺序可以直接读它。

## 四种角色

只有四个角色，不做用户组，也不做细粒度权限。自部署实例的用户数是个位数，一张表能装下的规则才会被真正遵守。控制台的「用户」页面（`/users`）渲染的就是同一张表，方便管理员一边分配角色一边对照。

| 权限 | admin | operator | viewer | demo |
|---|---|---|---|---|
| 调用平台接口 | 是 | 是 | 是 | 是 |
| 查看总览、资料库、下载、日志与系统信息 | 是 | 是 | 是 | 是 |
| 查看身份池、代理、API 密钥与设置 | 是 | 是 | 是 | 否 |
| 管理身份池与代理 | 是 | 是 | 否 | 否 |
| 创建与撤销 API 密钥 | 是 | 是 | 否 | 否 |
| 执行自检 | 是 | 是 | 否 | 否 |
| 修改运行配置 | 是 | 是 | 否 | 否 |
| 修改敏感配置：白名单、CORS、下载代理 | 是 | 否 | 否 | 否 |
| 管理账号与角色 | 是 | 否 | 否 | 否 |
| 创建与恢复备份 | 是 | 否 | 否 | 否 |

角色是阶梯而不是集合：`demo < viewer < operator < admin`，所以管理员能做运维员能做的一切，任何端点都不必把四个角色列一遍。

`demo` 排在 viewer **之下**，这一点是整个演示模式安全性的落点。所有权限门默认都是「viewer 或更高」，所以一个排在下面的角色天然被它们全部拒绝，一行调用点都不用改；日后新加的路由，在有人主动放开之前对演示实例都是关着的。它还额外受一条只读规则约束：除了少数几个「形态上是写、实质上是读」的接口（解析链接、本地计算的工具），所有写操作一律拒绝。完整说明见[安全](./15-security.md#演示模式)。

由此引出三个容易让人意外的结论：

- **角色变更在下一次请求就生效。** 主体在每次调用时都从数据库重建，没有缓存需要等待，也不需要把人踢下线。
- **控制台不会按角色隐藏页面**——`demo` 是唯一的例外。任何已登录用户都能打开导航里的每个页面，真正拒绝操作的是服务端。演示账号的侧边栏被裁剪过，因为一个陌生人面对一堆必然 403 的链接学不到任何东西；而运维者看到「用户」链接却被拒绝，反倒能了解自己实例的状态。「用户」页面对非管理员隐藏账号列表并禁用按钮，因为一个所有控件都会报错的页面，比一个说明原因的页面更糟。
- **`demo` 角色不能手动指定。** 它由 `demo.enabled` 这个开关生成，用户接口会拒绝把任何账号创建或改成这个角色——否则就会出现第二个演示账号，密码是管理员自己选的、也没有配套的 API 密钥。

## 管理控制台账号

账号管理只有管理员能做，全部集中在 `/api/v1/admin/users` 和控制台的「用户」页面。

| 操作 | 端点 | 谁可以 |
|---|---|---|
| 列出账号 | `GET /api/v1/admin/users` | admin |
| 创建账号 | `POST /api/v1/admin/users` | admin |
| 修改角色，或重置密码 | `PUT /api/v1/admin/users/{user_id}` | admin |
| 删除账号 | `DELETE /api/v1/admin/users/{user_id}` | admin |

### 创建账号

控制台里是「用户」页面上的**新增用户**，需要填用户名、初始密码和角色。用 API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/users \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"username": "ops-alice", "password": "correct-horse-battery", "role": "operator"}'
```

这里的 `cookies.txt` 就是[控制台会话](#控制台会话)一节里那条登录命令写下的 cookie 文件；只要所属账号是管理员，用一把带 `admin` scope 的 API 密钥同样可以。

| 字段 | 规则 |
|---|---|
| `username` | 3 到 64 个字符，需匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$`：首字符是字母或数字，其余可用字母、数字、点、短横线和下划线。全实例唯一。 |
| `password` | 8 到 256 个字符。以 argon2id 摘要存储，永不返回。 |
| `role` | `admin`、`operator` 或 `viewer`。不填默认 `viewer`。 |

用户名不能改。它是账号在审计日志里的稳定名字，改名等于改写历史；确实需要另一个名字，就新建一个账号再把旧的删掉。

### 修改角色

用「用户」表格每一行上的角色下拉框，或者 `PUT` 一个 `{"role": ...}`。其它什么都不变，也不会登出任何会话。

### 重置他人的密码

`PUT` 一个 `{"password": ...}`。这是管理员替忘记密码的人做的重置，它刻意**不**要求提供当前密码——那道校验在这里毫无意义，只会挡住想帮忙的管理员。该账号打开过的所有会话都会被撤销，包括他此刻正坐在前面的那个。响应里的 `revoked_sessions` 会告诉你撤销了几个。

修改**自己**的密码走的是另一个端点，并且必须提供当前密码——见[密码](#密码)。

### 删除账号

控制台会要求你手动输入用户名来确认。删除一个账号会：

- 撤销它打开过的所有会话；
- **删除它拥有的所有 API 密钥**，由数据库级联完成。这一条最容易踩坑：清退一位同事的同时，也会让他签发过的每个脚本停摆。要么先用别的账号补发一把密钥，要么定时任务会在人离开的同一刻一起熄火；
- 保留审计日志。审计记录对用户的外键是 `ON DELETE SET NULL`，所以某人做过什么不会随账号一起消失。

### 两条无法商量的保护

自部署实例没有客服台来撤销误操作，所以有两类操作会被直接拒绝：

- **不能删除你当前登录的账号。** 换一个管理员账号登录再删。
- **最后一个管理员不能被降级，也不能被删除。** 一个没有管理员的实例，只能从容器内的 shell 里救回来。

两者都会以 `INVALID_PARAM` 返回并指明字段，而不是悄无声息地什么都不做。

### 在 shell 里做同样的事

CLI 直接读写数据库，不需要控制台会话。所有人都登不进去时，它就是救援通道。

| 命令 | 作用 |
|---|---|
| `dtk user list` | 列出账号及其角色、创建时间和最近登录时间。绝不打印摘要。 |
| `dtk user create <username> --role admin\|operator\|viewer` | 创建账号，会提示输入两次密码。`--role` 默认是 `admin`。 |
| `dtk user passwd <username>` | 重置密码，同样提示输入两次。 |

两个写命令都支持 `--stdin`，从标准输入精确读取一行来代替交互提示，方便 `docker exec` 和自动化脚本：

```bash
printf '%s\n' 'correct-horse-battery' | \
  docker compose -p dtk -f docker/compose.yml exec -T api \
  dtk user create ops-alice --role operator --stdin
```

CLI 的密码下限与控制台一致，都是 8 个字符。它用 argon2id 按 OWASP 推荐参数计算（19 MiB、2 轮迭代、1 条并行通道），而 API 用的是 argon2 库自身的默认值。这个差别不要紧，因为参数被编码在每条摘要里：CLI 设的密码可以在控制台验证通过，反之亦然；用旧参数写下的摘要会在下一次成功登录时就地升级。

CLI 并不知道"最后一个管理员"这条保护——它只管写数据库。所以 `dtk user create` 也是你把自己降级之后的退路。参见[命令行参考](./13-cli.md)。

## 密码

| 规则 | 取值 | 生效位置 |
|---|---|---|
| 最短长度 | 8 个字符 | 服务端、控制台表单和 CLI |
| 最长长度 | 256 个字符 | 服务端 |
| 算法 | argon2id | `src/dtk/api/routes/passwords.py` |
| 重新哈希 | 参数升级后，下一次成功登录时自动完成 | 登录 |
| 其它（字典校验、定期轮换、复杂度） | 不做强制 | 有意为之：这是跑在你自己机器上的工具，不是企业目录服务 |

有两条规则是刻意缺席的，而且都属于容易被人"顺手补上"的那种：没有密码有效期，长度下限之外也没有复杂度要求。对一个用户群就是部署者本人的工具来说，这两条都无法带来真正的收益。

### 修改自己的密码

`POST /api/v1/auth/password`，或者控制台「用户」页面上的表单。即使你已经登录，它仍然要求输入当前密码——被盗的会话不应该足以接管账号；同时它直接拒绝 API 密钥，这样泄露的密钥无法把主人锁在自己的控制台外面。

成功后该账号的**其它**会话全部登出，你正在用的这个保留。改密码的常见原因就是它可能泄露了，而只撤销一半等于没撤销。

### 所有人都登不进去时

这里没有找回密码的邮件，也没有客服。有的是一个 shell：

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd admin
```

这就是全部的恢复路径，也是这个命令存在的理由——否则唯一诚实的说法只能是"把数据库删了重来"，为了一个忘掉的字符串，丢掉全部身份、快照和配置。

### 登录限流

登录失败会从三个维度计数。三者都是防滥用手段，都不是授权机制。

| 计数器 | 阈值 | 效果 | 说明 |
|---|---|---|---|
| 按用户名 | 5 次失败 | 拒绝 15 分钟 | 始终生效。代价只落在被猜的那个账号上，不波及其它。 |
| 按来源地址 | 20 次失败 | 拒绝 15 分钟 | **仅当** `DTK_FORWARDED_ALLOW_IPS` 被设为非通配符的值时才强制执行。否则只记一条 `auth.login_spray_suspected` 日志，不做拦截。 |
| 全部账号合计 | 60 秒内 30 次失败 | 之后每次尝试在校验密码前先等 2 秒 | 是延迟，不是拒绝。 |

按地址计数默认不拦截是有意为之。在 Docker 发布端口的后面，全世界的请求都来自网桥网关；在 TLS 终止层后面，所有请求都来自那台终止层。以这个地址来拦截，意味着陌生人二十次垃圾尝试，就能把唯一的管理员反复锁在自己的控制台外面十五分钟。把 `DTK_FORWARDED_ALLOW_IPS` 设成你反向代理的地址，地址才重新代表"一个调用方"，这个计数器才重新变成拦截。通配符不算声明：它是在告诉 uvicorn 无条件相信任何人发来的 `X-Forwarded-For`，结果是地址从"被共享"变成"可伪造"，那比共享更糟。

密码校验本身还被限制为最多 4 个并发。按当前参数，argon2id 每次大约要消耗 30 毫秒 CPU——这是设计使然——而一个每来一个请求就启动一次校验的开放端点，就是一条 CPU 耗尽通道。

## 控制台会话

| 属性 | 取值 |
|---|---|
| Cookie 名 | `dtk_session` |
| 内容 | 一个不透明的随机令牌；令牌 → 用户的映射存在 Redis 里 |
| 有效期 | 自登录起 7 天，**不会**因为使用而延长 |
| `HttpOnly` | 是 |
| `SameSite` | `Lax` |
| `Secure` | 请求是 HTTPS，或带 `X-Forwarded-Proto: https` 时置上；否则去掉，并在日志里留下 `auth.cookie_insecure` 警告 |
| `Path` | `/` |

因为会话存在 Redis 里，清空 Redis 会把所有人登出。事故处置时这是特性，维护窗口里这是惊喜，代价不过是重新登录一次。

有效期是绝对的。使用控制台并不会把到期时间往后推，所以一个开了八天的浏览器标签页，哪怕你今天早上还在用，也会重新要求输入密码。

| 操作 | 端点 |
|---|---|
| 登录 | `POST /api/v1/auth/login` |
| 登出当前会话 | `POST /api/v1/auth/logout`（幂等） |
| 我是谁 | `GET /api/v1/auth/me` |
| 列出我的在线会话 | `GET /api/v1/auth/sessions` |
| 登出其它所有设备 | `DELETE /api/v1/auth/sessions` |
| 撤销某一个会话 | `DELETE /api/v1/auth/sessions/{session_ref}` |

「用户」页面每个在线会话一行，显示它的地址、客户端和最近活跃时间。会话由 `session_ref` 标识——那是令牌的 16 位摘要，所以"列出我的会话"永远不会顺手交出一份可用凭据，这也是控制台敢把这个标识完整显示出来的原因。

`DELETE /api/v1/auth/sessions` 会保留发起调用的那个会话，所以你不会因为用了它而把自己登出。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{"username": "admin", "password": "..."}'
```

响应里带着登录成功的主体信息和 `expires_in: 604800`。

## API 密钥

密钥用于给程序做认证：脚本、定时任务、MCP 客户端、手机上的快捷指令。控制台自己从不使用密钥。对应页面是控制台「访问控制」分组下的「API Key」页面（`/api-keys`）。

### 创建密钥

控制台里是「API Key」页面上的**创建密钥**。用 API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/api-keys \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
        "name": "nightly-archiver",
        "scopes": ["douyin:read", "archive:read"],
        "rate_limit": 60,
        "expires_at": "2027-01-01T00:00:00Z"
      }'
```

| 字段 | 规则 |
|---|---|
| `name` | 1 到 128 个字符。它会出现在密钥列表和审计日志里，所以按将要持有它的脚本或主机来命名。 |
| `scopes` | 下文列出的那些。控制台要求至少选一个；API 允许传空列表，那会得到一把能通过认证、却被所有带 scope 校验的端点拒绝的密钥。 |
| `rate_limit` | 每分钟请求数，1 到 100000，或者 `null` 表示沿用实例默认值。 |
| `expires_at` | 未来的 ISO 时间戳，或 `null` 表示永不过期。过去的时间戳会被 `INVALID_PARAM` 拒绝。 |

控制台提供的有效期选项是永不过期、7、30、90、365 天；API 接受任何未来的时刻。

创建密钥走 `manage_pool`：需要 **operator** 及以上角色；如果你是用密钥而不是会话来调用的，还需要 `admin` 或 `identity:manage` scope。密钥永远属于创建它的人，没有任何办法替别人签发一把密钥。

### 密钥的样子，以及唯一一次能看到它的时刻

一把密钥长这样：

```
dtk_9f3c1a7b2e08_Vn4kQ2xR8sT1yU6wZ0aB3cD5eF7gH9jK
```

`dtk_` 加 12 个字符的前缀，再加密钥本体。只有前缀以明文存储、用于显示，密钥本身只以整串的 sha256 摘要保存。**完整值只存在于一个 HTTP 响应里，就是创建它的那个响应。** 服务端此后没有能力再把它显示给任何人，管理员也不行——已经没有东西可显示了。控制台的创建对话框不会因为误点背景而关闭，并且要求你勾选一个确认项，因为值丢了之后唯一的补救就是撤销这把、再签一把。

### 列出密钥

`GET /api/v1/admin/api-keys` 返回**实例上的全部密钥**，最新的在前；加 `?mine_only=true` 只看自己的。读取列表走 `read_admin`——任何已登录的控制台用户，包括 viewer，都能看到所有人密钥的名称、前缀、归属、scope、限额和最近使用时间。响应里没有任何密文，但这份清单在账号之间并不保密。控制台页面另外提供了搜索框以及按状态、按 scope 的筛选，全部在浏览器里对完整列表求值。

`last_used_at` 会在该密钥每一次通过认证的请求上写入，所以撤销之前想确认"还有东西在用它吗"，看这一列最快。

## 八个 scope

一个 scope 就是一组端点。只要密钥持有某个端点接受的任意一个 scope，它就能访问该端点。

| Scope | 打开了什么 |
|---|---|
| `douyin:read` | 抖音的全部读取：`GET /api/v1/douyin/video`、`/video/comments`、`/video/comments/replies`、`/user`、`/user/posts`、`/user/likes`、`/mix/posts`；针对抖音链接的 `POST /api/v1/parse` 和 `POST /api/v1/tasks/batch`；签名工具 `POST /api/v1/tools/sign`、`/decode`、`GET /api/v1/tools/parse-url`、`POST /api/v1/tools/parse-batch`；`POST /api/v1/archive/recheck` 与 `/archive/backfill`；以及回读上述调用创建的任务。 |
| `tiktok:read` | TikTok 上的同一组，外加只有 TikTok 提供的 `/user/followers` 和 `/user/following`。只持有这个 scope 的密钥访问 `/api/v1/douyin/...` 会被拒绝，REST 和 MCP 一视同仁。 |
| `archive:read` | 本实例已经存下来的内容，直接从 Postgres 返回、不碰平台：`GET /api/v1/archive`、`/archive/stats`、`/archive/collections`、`GET /api/v1/archive/{platform}/{content_id}`。刻意与平台读取权限分开——开放一个平台端点，不应该顺带开放这台实例历史上采集到的一切。 |
| `archive:export` | `GET /api/v1/archive/export`，一次请求遍历整个归档。它单独占一个 scope，因为这是唯一一个能把只读密钥变成数据库副本的调用。 |
| `media:read` | 下载记录及其字节内容：`GET /api/v1/downloads`、`/downloads/storage`、`/downloads/{download_id}` 和 `/downloads/{download_id}/files/{name}`。它还会让归档列表 `GET /api/v1/archive` 多返回本地媒体那一块。 |
| `media:write` | 一切发起或改变下载的操作：`POST /api/v1/downloads`、`/downloads/retry`、`/downloads/deduplicate`、`POST /api/v1/downloads/{download_id}/pin`、`DELETE /api/v1/downloads/{download_id}`——以及素材库的合集（`POST`、`PATCH`、`DELETE /api/v1/archive/collections...`）和 `POST /api/v1/archive/delete`。与 `media:read` 分开，是因为发起一次下载会消耗一个身份、占用磁盘：它是这套 API 里唯一一个长得像读、却对宿主机留下持久副作用的调用。 |
| `identity:manage` | 身份池及其周边机制：`/api/v1/admin/identities/*`、`/api/v1/admin/proxies/*`、`/api/v1/admin/watchlist/*`、`POST /api/v1/admin/diagnose`、`POST /api/v1/admin/notifications/test`、API 密钥这几个路由本身、只读的管理面板（配置列表、请求日志、审计日志、指标、接口访问控制）、`POST /api/v1/tools/identity`，以及数据调用上的 `identity=` 和 `explain=` 参数。不要把它给普通的只读密钥：`GET /api/v1/admin/identities/{id}/cookies/reveal` 会把解密后的 Cookie 罐交出来。 |
| `admin` | 一切。scope 校验遇到它直接短路，所以持有 `admin` 的密钥能通过实例上的每一道 scope 关卡。它唯一做不到的是抬高所属账号的角色——见下文。 |

有三条推论值得记牢：

- **一次读取里，平台这一半会被校验两次。** `/api/v1/parse` 和 MCP 工具在还不知道链接属于哪个平台时就已经收下了它，所以它们先问"这把密钥能读*某个*平台吗"，等平台解析出来之后再问一次。用只有 `tiktok:read` 的密钥去粘一个抖音链接，得到的是 `FORBIDDEN_SCOPE`，而不是一个空结果。
- **读取任务结果，需要创建它时所需的那个 scope。** 任务 id 不是它自己载荷的通行证；`GET /api/v1/tasks/{task_id}` 会按该任务所属端点重新校验 scope。
- **`GET /api/v1/auth/me` 不需要任何 scope。** 任何有效凭据都能调用，它会返回角色、scope 和限流值，因此这是脚本确认"我的密钥还好用吗"的标准做法。

## 密钥无法绕过的规则

**即便密钥属于管理员，scope 依然照查。** 自部署实例上几乎每一把密钥都属于 admin 账号，所以"管理员绕过 scope"这种捷径，会让 `archive:export`——那个唯一能交出数据库副本的调用——恰好在它被设计来防护的部署形态里彻底失效。代码没有走这条捷径。

**密钥不可能被授予其创建者本身不持有的 scope。** 当发起签发的调用方本身也是一把密钥时，请求的 scope 会与它持有的取交集，多出来的部分以 `FORBIDDEN_SCOPE` 拒绝，并列出被拒的和实际持有的。没有这条，一把只有 `identity:manage` 的密钥——它被 `GET /api/v1/admin/users` 拒绝、也被敏感配置的写入拒绝——就可以给自己签一把 `scopes: ["admin"]` 的密钥，前后两个请求就把两件事都做了。控制台会话不受这一条约束，因为它本来就由角色而不是 scope 来限定，而且该路由已经要求 operator 角色。

**密钥携带其所属账号的角色，角色门槛依旧生效。** 所以 operator *可以*签出一把带 `admin` scope 的密钥，而这把密钥仍然够不到 `/api/v1/admin/users`，也改不了敏感配置——那些地方查的是所属账号的角色，读到的是 `operator`。把一个账号降级，它名下所有密钥在下一次请求就同步降权；把账号删掉，密钥一起消失。

**密钥不能修改密码**，无论是主人的还是别人的。那个端点只接受控制台会话。

**密钥不适合转手给别人。** 密钥没有对象级归属：任何 operator 都能撤销任何密钥，任何持有 `identity:manage` 的密钥都能把请求指定到池子里的任意身份上。如果两个人需要各自独立的影响范围，请给他们两个账号，而不是一个账号下的两把密钥。

## 限流不是计费

每把密钥都可以带一个 `rate_limit`，单位是每分钟请求数。**它是防滥用手段，绝不是计费。** 本项目没有套餐、没有用量计量、没有结算，也没有多租户；这个限额存在的唯一目的，是防止一个写错的脚本把大家共用的身份池打穿。如果你在找一个可以用来计费的数字，这里没有。

| | 取值 |
|---|---|
| 窗口 | 固定 60 秒，按墙上时钟对齐 |
| 计数维度 | 密钥是 `key:<key id>`，控制台会话是 `user:<user id>`，匿名调用是 `anon:<address>` |
| 密钥的限额 | 密钥自己的 `rate_limit`，没有则用 `api.default_rate_limit_per_min` |
| 会话的限额 | 始终是 `api.default_rate_limit_per_min` |
| 默认值 | 每分钟 `120` 次 |
| 何时不限流 | 生效限额小于等于 `0` 时 |
| 响应头 | `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`（Unix 秒） |
| 超限时 | `429`，错误码 `RATE_LIMITED`，并带 `Retry-After` 头 |

两点要注意：

- 控制台会话同样被计量，按实例默认值、以账号为单位。开了好几个控制台标签页的浏览器共用这一个桶。
- 匿名调用按来源地址分桶，而在 Docker 发布端口或 TLS 终止层后面，所有人都是同一个地址。此时这个桶会退化成一个共享计数器，而不是退化成"没有计数器"——对一个以防滥用为职责的计数器来说，往严格的方向退化才是对的。

`api.default_rate_limit_per_min` 是运行时配置，见[配置参考](./03-configuration.md)。

## 过期与撤销

过期的密钥会自己停止工作，这能限制"没人察觉的泄露"造成的损失。过期在认证时与存储的时间戳比对，因此不需要清理任务，且精确到秒生效。

撤销是 `DELETE /api/v1/admin/api-keys/{key_id}`，对应每行上的**撤销**按钮，或者勾选多行后的批量操作。它需要 operator 角色，并且对任何人的密钥都有效。

- 它在**下一次请求**生效。认证每次都会读取该行记录，没有缓存需要等待。
- 它不可撤销。被撤销的密钥就是撤销了；请另签一把。
- 还在用这把密钥的东西会开始收到 `401 UNAUTHENTICATED`。

列表里的状态列由记录本身推导：`active`、`expired`（`expires_at` 已过）或 `revoked`。撤销一把已经撤销的密钥不会有任何改变，但仍然会写一条审计记录，detail 里带 `already_revoked: true`。

按脚本或主机一把一把地签发。这样一次泄露就只是一次撤销，而审计日志里的名字会直接告诉你该去清理哪台机器。

## 程序如何认证

三种方式，服务端按以下顺序尝试：

1. `Authorization: Bearer dtk_...`
2. `X-API-Key: dtk_...`
3. `dtk_session` cookie

```bash
# 两个请求头效果相同，任选其一。
curl -H 'X-API-Key: dtk_9f3c1a7b2e08_...' \
  'http://127.0.0.1:8000/api/v1/douyin/video?url=https://v.douyin.com/xxxxxxx/&wait=20'

curl -H 'Authorization: Bearer dtk_9f3c1a7b2e08_...' \
  http://127.0.0.1:8000/api/v1/auth/me
```

有两个行为要知道：

- **密钥优先于 cookie。** 如果一个请求两者都带且密钥有效，胜出的是密钥的主体——包括它更窄的 scope。如果密钥是未知的、过期的或已撤销的，请求会退回去用 cookie。
- **错误的凭据绝不会被降级成匿名。** 携带了一把服务端不认的密钥的请求，即使打在已开放给匿名调用的端点上，也会明确收到 `401`。悄悄降级会把"你的密钥过期了"变成"你的密钥能用但看得更少"，后者要难查得多。

`/mcp` 端点只接受 API 密钥（两个请求头都行），你要是送了会话 cookie，它会明确告诉你。密钥必须持有 `douyin:read` 或 `tiktok:read` 才能完成握手；随后每个工具会针对它实际被要求的平台再校验一次。见 [MCP 与 AI 客户端](./12-mcp.md)。

### 请求被拒绝时

| 状态码 | `error.code` | 含义 |
|---|---|---|
| `401` | `UNAUTHENTICATED` | 没有凭据，或服务端不认识的凭据，或已过期、已撤销的凭据。在 `/mcp` 上响应还会带 `WWW-Authenticate: Bearer realm="dtk"`。 |
| `403` | `FORBIDDEN_SCOPE` | 凭据有效，但缺少所需的 scope（`error.details.required` 会列出能通过的 scope），或者账号角色不够（`error.details.required_role` 会指出需要的角色）。 |
| `429` | `RATE_LIMITED` | 超过了每分钟限额。`Retry-After` 给出需要等待的时长。 |

所有错误使用与成功响应相同的信封：`success`、`data`、`error`、`meta`。请按 `error.code` 分支判断，绝不要解析 `error.message`——那是本地化文本。

## 公开端点

默认情况下**每一个**端点都需要凭据，而这是正确的默认值：放在公网服务器上的实例，是陌生人能够到的机器，而它的全部用途就是消耗别人的身份池。

有些部署仍然想开放一小部分——防火墙后的个人实例、只读镜像、嵌在页面里的链接解析小工具。对应的配置是 `api.public_endpoints`：一个由 `"<METHOD> <path template>"` 字符串组成的列表，写法必须与 API 文档里的拼写完全一致。

```json
["GET /api/v1/{platform}/video", "POST /api/v1/parse"]
```

注意写的是路径模板而不是具体路径：是 `{platform}`，不是 `douyin`。条目会与 OpenAPI 文档中该路由的名字做匹配。

### 请用控制台页面，而不是手写文本框

控制台的「接口访问控制」页面（`/endpoint-access`）按 tag 分组列出了每一个有文档的操作，每行一个开关。这些行是从正在运行的应用自己的 OpenAPI 文档构建出来的，所以开关和配置不可能对"这个路径叫什么"产生分歧——这一点很重要，因为手写条目里的拼写错误是静默失败的：它只是永远匹配不上，而你本想开放的那个端点会一直关着，直到有人发现。

每个开关的含义都是「这个接口需要 API Key」，所以 ON 才是受保护状态，全开即全安全：开关打开时该行显示**需要 Key**，关掉之后显示**无需 Key**。把某个开关关掉才是移除凭据校验，也正是这个方向会让页面要求你确认。

`api.public_endpoints` 被标记为 SENSITIVE，因此写入它需要管理员、`admin` scope，以及请求体里的 `confirm: true`。控制台会从自己的确认对话框补上这个确认。每一次改动都会以 `settings.updated_sensitive` 写入审计日志。

### 永远无法开放的部分

无论配置怎么写，以下三个前缀都会被拒绝。没有任何开关可以覆盖。

| 前缀 | 原因 |
|---|---|
| `/api/v1/admin` | 身份、代理、用户、API 密钥、配置、备份。开放其中任何一个，等于把整台实例交出去。 |
| `/api/v1/auth` | 登录、会话、改密码。 |
| `/api/setup` | 首次初始化，也就是创建第一个管理员的地方。 |

写了这些路径的条目会在解析配置时被丢弃，并记一条 `api.public_endpoints.refused` 日志；控制台把这些行渲染成锁定状态，根本不给开关，而不是给一个按了没用的开关。这条禁令写死在代码里、不交给你判断，是因为它的失败方式既不可恢复又无声无息：一个恰好命中管理路由的错别字，在被人发现之前不会有任何迹象。

### 开放某个路由是必要条件，不是充分条件

未认证的调用方运行在一个匿名主体上，它**恰好持有两个 scope**：`douyin:read` 和 `tiktok:read`。永远没有 admin，永远没有写权限。

所以在配置里开放 `GET /api/v1/downloads` 毫无意义：该路由依然要求 `media:read`，匿名主体没有它，调用方拿到的是 `403` 而不是 `401`。实际上真正适合开放的，是平台读取端点、`/api/v1/parse`、`/api/v1/tasks/batch`、`/api/v1/tools` 下的链接与签名工具、用于回读上述任务的 `/api/v1/tasks/{task_id}`，以及 `/api/v1/system/status`。

匿名调用按来源地址而不是按密钥限流，因此一个开放端点不会变成对身份池的无限抽取——注意事项见[限流不是计费](#限流不是计费)里关于共享地址的说明。

### 从未上锁的路由

有少数几个路由在代码里根本没有凭据校验，所以无论配置怎么写它们都对外应答，任何开关都关不掉。控制台把它们单独列出，这是对的：它们没有一个会去平台上取数据。

| 路由 | 原因 |
|---|---|
| `POST /api/v1/auth/login` | 登录端点不可能要求你先登录。 |
| `POST /api/v1/auth/logout` | 幂等；登出两次不算错误。 |
| `GET /api/setup/status` | 初始化向导会在账号存在之前先问它。 |
| `POST /api/setup/init` | 创建第一个管理员，改由打印在容器日志里的一次性初始化令牌（setup token）把关——它在哪里出现见[快速开始](./01-quickstart.md)。 |
| `GET /api/v1/ios/shortcut` | iOS 快捷指令在还没有地方放密钥的时候就要问它的版本说明。返回的只是公开的发布信息。 |

`/healthz` 和 `/readyz` 同样不需要认证，而且根本不在 API 文档里，所以不会出现在这个页面上。

### 什么时候不该这么做

在一台谁都能访问的实例上开放平台读取端点，意味着陌生人在消耗你的身份池、你的代理带宽和你的频率预算，而请求日志会把这些全部归到同一个匿名桶里。如果你真正想要的是"让我另一台服务器不带密码调用它"，那么一把只有一个 scope、限额很低的 API 密钥在各方面都更好：它可归因、可以单独撤销，而且不依赖来源地址有任何意义。更完整的暴露面清单见[安全](./15-security.md)。

## 会被记录下来的内容

敏感操作会写入审计日志，可以通过 `GET /api/v1/admin/audit` 读取，也可以在控制台「日志」页面（`/logs`）的「审计日志」标签页里查看。

| 动作 | 何时写入 |
|---|---|
| `user.created` | 创建账号 |
| `user.updated` | 管理员修改角色或重置密码 |
| `user.deleted` | 删除账号 |
| `user.password_changed` | 有人修改自己的密码 |
| `user.sessions_revoked` | 有人登出自己的其它设备 |
| `api_key.created` | 签发密钥，记录名称、前缀、scope 和限额 |
| `api_key.revoked` | 撤销密钥 |
| `settings.updated_sensitive` | 修改 SENSITIVE 配置，包括 `api.public_endpoints` |

每条记录都会写下是谁做的（账号，以及可能的密钥 id）、动了什么、来源地址和 User-Agent。它从不记录凭据本身：detail 描述的是*什么发生了变化*，密钥的值或 Cookie 罐不在其中。审计记录比它提到的账号和密钥活得更久，因为两个外键都是 `ON DELETE SET NULL`。

另外，每一次对上游的请求都会在请求日志里写一行，带上引发它的 `api_key_id`，这就是把身份池消耗归因到具体某个脚本的方式。这部分内容见[运维](./10-operations.md)。

## 下一步

- [配置参考](./03-configuration.md) —— `api.default_rate_limit_per_min`、`api.public_endpoints` 以及其它运行时配置。
- [控制台总览](./05-console-overview.md) —— 其余那些页面。
- [REST API 指南](./11-api.md) —— 这些 scope 所把守的端点。
- [MCP 与 AI 客户端](./12-mcp.md) —— Agent 如何携带密钥。
- [命令行参考](./13-cli.md) —— `dtk user` 及其它命令。
- [安全](./15-security.md) —— 暴露面、密钥管理与静态加密。
