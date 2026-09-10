# Security policy / 安全策略

**English first, 中文在后。**

---

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.** Use one of these instead:

- [Open a private security advisory](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/security/advisories/new) — preferred; it gives us a private thread and a CVE if one is warranted
- Email `Evil0ctal1985@gmail.com`

Please include the version, how the instance is deployed, and enough detail to
reproduce. If you have a proof of concept, send it — but describe the class of
problem in the summary rather than only attaching an exploit.

You will get a first reply within a few days. This is a project maintained by one
person in their own time, so a fix is not instant, but a report is never ignored.
If a report turns out to be a real issue you will be credited in the advisory
unless you ask not to be.

## Supported versions

| Version | Status |
|---|---|
| v5 (`main`) | Supported. Fixes land here. |
| v4 (`v4` branch) | Frozen. Security fixes only, and only for issues that are exploitable in a default install. |
| Anything older | Unsupported. |

There is no upgrade path from v4 to v5 — different schema, different
configuration, different container layout. See the
[v5.0.0 release notes](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.0.0).

## What is in scope

This is software you self-host. In scope is anything that lets someone do
something the instance's own access control says they cannot:

- Authentication or session handling — logging in as someone else, keeping a
  session past a revocation, escalating a role
- API key scopes — a key reaching an endpoint outside its scopes
- The demo role — a demo session reaching anything that writes, or reading
  anything the four read-only pages do not expose
- Credential exposure — cookie jars, proxy URLs, the instance key or an API key
  becoming readable when they should not be. Stored credentials are encrypted
  with the instance key; a database dump alone should not carry them
- SSRF in the downloader or the URL allowlist, path traversal in the archive or
  media paths, injection anywhere a caller-supplied value reaches SQL or a shell
- Container escape or privilege escalation out of the shipped compose file

## What is not in scope

- **What the platforms do.** Rate limiting, risk control, an endpoint that stops
  answering, a signature algorithm that changed — those are
  [endpoint issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/new?template=2-endpoint-zh.yml),
  not vulnerabilities.
- **Configuration you chose.** Binding the API to `0.0.0.0` on a public host with
  no proxy, publishing Postgres, reusing a secret key across installs, running
  with a weak administrator password. The documentation says what each of these
  costs; choosing it anyway is a deployment decision, not a bug in the software.
- **The absence of a feature.** No 2FA today is a missing feature; say so in a
  feature request and it will be treated as one.
- Reports produced entirely by a scanner, with no analysis of whether the finding
  is reachable in this codebase.

## What the software does for you

Short version, so you know what to expect before you test:

- Nothing ships a default password or key. The process refuses to start without
  `DTK_SECRET_KEY`, and refuses a key shorter than 32 characters.
- Every stored credential — cookie jars, proxy URLs — is encrypted with the
  instance key.
- API keys are scope-bounded; the console session is role-bounded. Roles are a
  ladder: `demo < viewer < operator < admin`.
- Demo mode is read-only by method, its credentials stop working the moment it is
  switched off, and its requests are not written to the request log or the
  archive.
- Containers run as a non-root user on a read-only root filesystem with dropped
  capabilities, and Postgres and Redis sit on an `internal: true` network.

The full account, including what is explicitly your responsibility rather than
the software's, is in [Security](./documents/en/15-security.md).

---

## 报告安全问题

**不要为安全问题开公开 issue。** 走下面两条之一：

- [提交私密安全公告](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/security/advisories/new) —— 推荐，会开一个私密讨论串，够格的话还能申请 CVE
- 发邮件到 `Evil0ctal1985@gmail.com`

请写清版本号、实例是怎么部署的，以及足够复现的细节。有 PoC 就一起发，
但摘要里请描述问题类别，不要只丢一个漏洞利用脚本。

几天内会有第一次回复。这个项目由一个人用业余时间维护，所以修复不会立刻发生，
但报告不会被无视。确认是真问题的话，会在公告里署你的名，除非你说不要。

## 受支持的版本

| 版本 | 状态 |
|---|---|
| v5（`main`） | 维护中，修复都落在这里 |
| v4（`v4` 分支） | 已冻结。只接受安全修复，且只针对默认安装下可被利用的问题 |
| 更早的版本 | 不再支持 |

v4 到 v5 没有升级路径 —— 表结构、配置方式、容器编排全都不同。见
[v5.0.0 发布说明](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.0.0)。

## 什么算在范围内

这是一套你自己托管的软件。凡是能让某人做到实例的访问控制说他不能做的事，都算：

- 认证与会话 —— 以他人身份登录、吊销后会话仍然有效、越权提升角色
- API Key 的权限范围 —— 一把 Key 能调到它作用域之外的接口
- demo 角色 —— demo 会话能触达任何写操作，或读到那四个只读页面之外的东西
- 凭据泄露 —— cookie jar、代理地址、实例主密钥或 API Key 在不该可读的时候变得可读。
  存下来的凭据都用实例主密钥加密，单独一份数据库导出不应该带着它们
- 下载器或 URL 白名单里的 SSRF、归档与媒体路径的目录穿越、以及任何调用方输入
  抵达 SQL 或 shell 的注入
- 从仓库自带的 compose 配置里逃逸出容器或提权

## 什么不算

- **平台自己的行为。** 限流、风控、某个接口不再返回数据、签名算法变了 —— 这些是
  [接口问题](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/new?template=2-endpoint-zh.yml)，
  不是漏洞。
- **你自己选的配置。** 在公网机器上把 API 绑到 `0.0.0.0` 且不挂代理、把 Postgres 暴露出去、
  多个实例复用同一把密钥、用弱口令当管理员密码。文档写清了每一条的代价，明知代价仍然这么选，
  那是部署决策，不是软件缺陷。
- **功能没做。** 目前没有两步验证，那是缺功能；提功能建议即可，会按功能建议处理。
- 纯扫描器跑出来、没有分析过这条结论在本项目代码里是否可达的报告。

## 软件本身替你做了什么

先说清楚，免得你测的是本来就不存在的东西：

- 仓库里不带任何默认密码或密钥。没有 `DTK_SECRET_KEY` 进程直接拒绝启动，
  少于 32 个字符也拒绝。
- 所有存下来的凭据 —— cookie jar、代理地址 —— 都用实例主密钥加密。
- API Key 带作用域，控制台会话按角色。角色是一条阶梯：`demo < viewer < operator < admin`。
- 演示模式按请求方法只读，关掉的那一刻其凭据立即失效，它的请求不写入请求日志和归档库。
- 容器以非 root 用户运行在只读根文件系统上并裁剪了 capabilities，
  Postgres 和 Redis 在 `internal: true` 网络上。

完整说明，包括哪些明确是你的责任而不是软件的，见
[安全](./documents/zh/15-security.md)。
