## What's new

**Browser signing no longer gets identities cooled for a fault on this side.**
browser-rpc signs under its own browser's User-Agent, but answered with the one
it was asked for, so an identity on another User-Agent - minted under an older
browser image, or imported from your own browser - got signatures TikTok
refuses, and dtk filed each refusal as risk control: the identity was cooled and
degraded. browser-rpc now says so instead of signing, and dtk signs that
identity natively where it can. Also: a TikTok signing page is not used before
it has an msToken of its own, so the request carries the page's fresh token,
which is what TikTok judges; and the archive's availability recheck stops
holding an identity locked after a failed check. These are the follow-ups to
#832.

### Upgrading

Installed with the script on 5.1.6 or later, in the install directory:

```bash
./dtkctl upgrade
```

On 5.1.5 or earlier, `dtkctl` has no `upgrade` yet, so this once:

```bash
cd /opt/dtk && git pull           # or ~/dtk - wherever you installed it
bash install/install.sh --upgrade # install/install.zh.sh for the Chinese one
```

It checks out `v5.1.13`, pulls 5.1.13, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.13` into `.env` itself; there is nothing to edit.

**Upgrade browser-rpc together with api and worker** - `./dtkctl upgrade` does.
Mixed versions fail: an api/worker from 5.1.12 or earlier against this
browser-rpc reports `signing_reference_failed` for both platforms in the
self-check and fails identities on another User-Agent, and this api/worker
against an older browser-rpc brings #832 back. If you build or run browser-rpc
separately, rebuild it from this release. The rebuild reuses its cached layers:
only browser-rpc's own code changed, and the CloakBrowser pin did not.

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.13 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

No new migrations, no new settings, and API keys need nothing. Your data is
untouched.

### Changed

- **browser-rpc reports the User-Agent its page signs under** - in
  `/rpc/health` and with every signature - and refuses a request naming one the
  platform would not accept that signature for, with `409 user_agent_mismatch`:
  on TikTok any other User-Agent; on Douyin one for which dtk's query would name
  another operating system. Measured on 2026-11-20: Douyin accepted the page's
  signature for identities on other Chrome versions and on Edge, and refused it
  0/11 for a query naming macOS under a Windows page. dtk then signs the
  identity natively, when `signing.fallback_enabled` allows it, native signing
  is available, and the request is not one only a browser can sign (a TikTok
  jar with no `msToken`, a Douyin sign-protected endpoint with no visitor id);
  otherwise the request fails as `SIGNING_FAILED`, below. In `rpc` mode the
  first such crossing on an endpoint raises a `signing.fallback.engaged` alert
  with the reason `browser-rpc cannot sign under this identity's User-Agent`.
  Nothing to do.
- **A TikTok signing page is handed out only once it seals a usable `msToken`
  of its own**, for an identity whose jar holds one. For its first 0.1-1.4 s
  after opening or switching identity - at least 4 s on some switches through
  the service - the page's SDK seals an empty token, and the request then went
  out with whatever token the jar held. Once the page has its own, the request
  carries that fresh token, and the token in the Cookie header is what TikTok
  judges. The first signature after a switch waits for it (0.8-3.5 s measured);
  later ones are as fast as before. A page whose SDK never gets a usable token
  now fails to open with `could not seal a usable msToken within …s` instead of
  being handed out: check the exit it loads through, as for a mint that fails
  on the same token. Not changed: a jar with no usable `msToken` gets no wait,
  and its first request after a switch is still refused - re-mint such an
  identity.
- **An empty `msToken` the page sealed now stays in the query**, so the query is
  exactly the one the signature covers. Nothing to do.
- **A refused User-Agent is named in the log.** `signing.rpc.user_agent_mismatch`
  names the identities browser-rpc refused, once per identity and outcome, and
  says in `not_native` why the native signer could not take it when the request
  failed. Such a failure is `SIGNING_FAILED` with `details.cause:
  user_agent_mismatch` and a message of its own; if your client retries
  `SIGNING_FAILED`, a retry helps only when it lands on another identity. The
  troubleshooting guide says what to do with such an identity - for one you
  imported, re-import it rather than retire it.
- **The self-check takes its browser reference under the browser's own
  User-Agent**, the one it always signed under. Nothing to do.

### Fixed

- **Identities on another User-Agent were cooled and degraded** whenever
  browser signing was used for them - in `rpc` mode, or in `auto` mode for a
  request only a browser can sign - because browser-rpc signed under its own
  User-Agent and dtk's check could never see it (#832). On Douyin only
  identities on another operating system were affected. Such an identity is now
  signed natively where native signing can take the request, and otherwise its
  request fails as `SIGNING_FAILED`, which cools nothing; Changed says what to
  do with it. Thanks to [@huynk-04](https://github.com/huynk-04).
- **The first TikTok request after a signing page opened or switched identity
  went out with the jar's token** rather than the page's fresh one (#832). See
  Changed. Nothing to do.
- **A failed availability recheck kept its identity locked** until a later
  check succeeded - up to the whole recheck run - so other requests on that
  identity waited behind it, and the cooldown it had earned stayed invisible
  meanwhile. The recheck commits after a failed check too (#832). Nothing to
  do.
- **An identity's first TikTok request could hold its row locked across the
  whole request** when the home page read for its device id set a cookie the
  jar did not hold. That cookie is now written with the request's own answer.
  Nothing to do.

---

## 本次更新

**浏览器签名不会再因为我们自己这边的问题让身份被冷却。** browser-rpc 一直用它自己浏览器的 User-Agent 签名，回复里却写的是请求方传来的那个 UA。于是 UA 不同的身份——在旧版浏览器镜像下铸造的，或者从你自己的浏览器导入的——拿到的签名会被 TikTok 拒绝，而 dtk 把每次拒绝都当成风控：身份被冷却、被降级。现在 browser-rpc 会直接说明签不了，dtk 在能用原生签名时改用原生签名。另外：TikTok 签名页在拿到自己的 msToken 之前不再被使用，这样请求带的是签名页新鲜的 token，而 TikTok 判的正是它；归档的可用性重新检查在一次检查失败后不再一直锁着身份。这些都是 #832 的后续修复。

### 升级方式

用脚本装的 5.1.6 及以后的版本，在安装目录里：

```bash
./dtkctl upgrade
```

5.1.5 及更早的版本，`dtkctl` 里还没有 `upgrade`，这一次要这样：

```bash
cd /opt/dtk && git pull           # 或 ~/dtk，装在哪就在哪
bash install/install.zh.sh --upgrade # 英文版用 install/install.sh
```

它会 checkout `v5.1.13`、拉取 5.1.13、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.13` 写进 `.env`，
不需要手改任何东西。

**browser-rpc 要和 api、worker 一起升级**——`./dtkctl upgrade` 就是一起升的。版本混用会出问题：5.1.12 及更早的 api/worker 配这个版本的 browser-rpc，自检里两个平台都会报 `signing_reference_failed`，UA 不同的身份也会失败；反过来，这个版本的 api/worker 配旧的 browser-rpc，#832 的问题又会回来。如果你单独构建或运行 browser-rpc，请用这个版本重新构建。重新构建会复用缓存层：只有 browser-rpc 自己的代码变了，CloakBrowser 的 pin 没变。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.13 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

没有新的迁移，没有新的设置，API Key 也不用动。数据不受影响。

### 变更

- **browser-rpc 会报告它的签名页实际使用的 User-Agent**——在 `/rpc/health` 里，也随每个签名返回——遇到平台不会接受这个签名的 UA 就拒绝，返回 `409 user_agent_mismatch`：TikTok 上是任何不同的 UA；抖音上是 dtk 构造的 query 会写成另一种操作系统的 UA。2026-11-20 实测：对其他 Chrome 版本和 Edge 的身份，抖音都接受签名页的签名；在 Windows 签名页下、query 写的是 macOS 时则全部拒绝（0/11）。之后 dtk 会用原生签名签这个身份，前提是 `signing.fallback_enabled` 允许、有原生签名可用，并且这个请求不是只有浏览器才能签的那种（jar 里没有 `msToken` 的 TikTok 身份，或者 jar 里没有访客 ID 时的抖音签名保护接口）；否则请求以 `SIGNING_FAILED` 失败，见下文。`rpc` 模式下，某个接口第一次这样转去原生签名时，会发出一条 `signing.fallback.engaged` 告警，原因是 `browser-rpc cannot sign under this identity's User-Agent`。不需要做任何事。
- **TikTok 签名页要封出自己的可用 `msToken` 才会被交出去使用**，这只针对 jar 里本来就有可用 token 的身份。签名页刚打开或刚切换身份后的 0.1-1.4 秒里——经由服务时有些切换至少 4 秒——SDK 封进去的是空 token，这时请求带的是 jar 里当时的那个 token。等签名页有了自己的 token，请求就会带上这个新鲜的 token，而 TikTok 判的正是 Cookie 头里的 token。切换身份后的第一次签名会等它到来（实测 0.8-3.5 秒），之后的签名和以前一样快。SDK 一直拿不到可用 token 的签名页，现在会以 `could not seal a usable msToken within …s` 打开失败，而不是被交出去：请检查它经由的出口，和铸造时卡在同一个 token 上的处理方法一样。没有变的是：jar 里没有可用 `msToken` 的身份不会等待，它在切换后的第一次请求仍然会被拒——请重新铸造这样的身份。
- **签名页封进去的空 `msToken` 现在会原样保留在 query 里**，这样 query 和签名覆盖的内容完全一致。不需要做任何事。
- **被拒绝的 User-Agent 会记录在日志里。** `signing.rpc.user_agent_mismatch` 会写明 browser-rpc 拒绝了哪些身份，每个身份每种结果记一次；请求失败时，`not_native` 会说明原生签名为什么也没能接手。这种失败返回 `SIGNING_FAILED`，带 `details.cause: user_agent_mismatch` 和专门的提示信息；如果你的客户端会重试 `SIGNING_FAILED`，只有重试落到别的身份上才会成功。这类身份怎么处理见故障排查文档——如果是你导入的身份，请重新导入，而不是退役它。
- **自检取浏览器参考签名时，用的是浏览器自己的 User-Agent**，也就是它一直以来实际签名用的那个。不需要做任何事。

### 修复

- **UA 不同的身份会被冷却、降级**：只要对它们使用了浏览器签名——`rpc` 模式下，或者 `auto` 模式下只有浏览器才能签的请求——就会这样，因为 browser-rpc 用它自己的 UA 签名，dtk 的检查永远看不出来（#832）。抖音上只有操作系统不同的身份受影响。现在原生签名能接手请求时，这样的身份会改用原生签名；接不了时，请求以 `SIGNING_FAILED` 失败，不会冷却身份；这类身份怎么处理见「变更」。感谢 [@huynk-04](https://github.com/huynk-04)。
- **签名页刚打开或刚切换身份后的第一个 TikTok 请求，带的是 jar 里的 token**，而不是签名页新鲜的 token（#832）。见「变更」。不需要做任何事。
- **可用性重新检查中一次检查失败后，会一直锁着那个身份**，直到后面某次检查成功——最长会锁到整轮检查结束——这期间这个身份上的其他请求都在等它，它应得的冷却也一直看不到。现在检查失败后也会提交事务（#832）。不需要做任何事。
- **身份的第一次 TikTok 请求可能在整个请求期间锁着这一行**：读取 device id 的首页种下了 jar 里没有的 Cookie 时就会这样。现在这个 Cookie 会随这次请求自己的结果一起写入。不需要做任何事。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.13` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.13` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-50f89e8ba23c205e06bfeed78246daf81583276b` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.13` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.13`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.12...v5.1.13
