## What's new

**TikTok videos download again, and TikTok identities stop going dead after
twenty-odd requests.** Every TikTok video link dtk handed out answered 403 to
every mirror: TikTok ties a video's links to the device id of the session that
fetched them, and dtk made up a new device id on every request. Separately, each
TikTok `msToken` has a use budget, and dtk replayed the one an identity was
minted with until TikTok refused it for good - the `signature.rejected` runs
that drained the pool in #830. Identities now keep the cookies the platform sets,
and a TikTok identity sends its own device id. Parse results also say what
fetching a media link directly takes, the browser self-check names what actually
went wrong, and the browser and transcriber images can be built through package
mirrors. Both platforms' web signing was checked against the live sites:
Douyin's needed no change, TikTok's now stamps the SDK build tiktok.com serves.

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

It checks out `v5.1.12`, pulls 5.1.12, backs the database up, migrates, restarts
and writes `DTK_IMAGE_TAG=5.1.12` into `.env` itself; there is nothing to edit.

**The browser image is rebuilt from the start once.** A new step runs before its
system packages, so the build downloads Chromium again: a few minutes, longer on
a small machine. The CloakBrowser pin is unchanged. If your build host reaches
Debian or PyPI slowly, set `APT_MIRROR` and `PYPI_MIRROR` first (see Added).

By hand:

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
# DTK_IMAGE_TAG=latest needs no edit; a pinned one becomes 5.1.12 - no `v`, see the tag table
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # with --profile browser
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

No new migrations, no new runtime settings, and API keys need nothing. Your
data is untouched.

### Added

- **`media.access` in every parse result** says what fetching the links directly
  takes: `referer` is the Referer the CDN wants (Douyin's `douyinvod.com` answers
  403 without `https://www.douyin.com/`), and `session_bound: true` marks a TikTok
  video, whose links open only with the session that fetched them - fetch those
  through `/api/v1/downloads`. Nothing to do unless you fetch media links
  yourself (#805, #829). Thanks to [@qigaowei](https://github.com/qigaowei),
  [@815074104](https://github.com/815074104) and
  [@bling-yshs](https://github.com/bling-yshs).
- **`APT_MIRROR` and `PYPI_MIRROR`** in `.env`, for build hosts where Debian or
  PyPI are slow or blocked: both reach the browser image's build, and
  `PYPI_MIRROR` also the transcriber's when it is built locally. Empty by
  default; a wrong value stops the build instead of quietly using the official
  servers. Set them, then rebuild (`./dtkctl build browser-rpc`). See Security
  for what a PyPI mirror is trusted with (#817). Thanks to
  [@duiaic](https://github.com/duiaic).

### Changed

- **Identities keep the cookies the platform sets.** What a response sets -
  refusals included, a 5xx or a dropped connection not - is written back to the
  identity, so the next request carries it, as a browser's would. Refused: a
  TikTok `msToken` shorter than 144 characters, a value that is not a valid
  cookie, a new cookie once the jar passes 8 KB, and the `msToken` a browser
  signing page sealed into a request. The worker logs the names
  (`identity.cookies.rotated`), never the values. Nothing to do.
- **A TikTok identity reads its device id once**, from TikTok's home page, as
  itself, the first time it is used - one extra request per identity, asked again
  after an hour if the page does not say. Nothing to do.
- **TikTok signatures stamp webmssdk 2.0.0.569**, the build tiktok.com serves
  now (was 2.0.0.561). TikTok does not check the build today, so nothing was
  failing for this; it was a tell. Nothing to do.
- **A failed download lists every mirror's failure**, as in `all 12 mirrors
  failed: v16-webapp-prime.us.tiktok.com answered 403 (11 mirrors); ...`, instead
  of only the last mirror's. If you match download errors on their text, match
  on this form too.
- **`dtk diagnose` names what the signing check found.** New codes:
  `signing_reference_failed` (warning: the browser produced no reference, so
  nothing was compared and the native signer is unaffected),
  `signing_native_failed` (failure) and `signing_build_drift` (warning: the
  browser signs as another TikTok SDK build). TikTok is compared on the build its
  `X-Dynosaur` names; the constant `X-Bogus=1` no longer counts as agreement. A
  comparison that crashed on every platform used to pass and now warns
  `signing_not_comparable`. A new warning here needs no action unless its text
  names one.
- **browser-rpc says what a failure implicates.** A signing page that left the
  platform says where it went (scheme and host only), a crash says so, and two
  attempts that failed alike name what they share - the exit, the platform's
  script or the browser. dtk passes browser-rpc's reason on instead of
  "unreachable", and a timeout reads `did not answer within Ns`. A mint whose
  page left the platform fails at once and says where it went, instead of
  waiting 18 s to report "msToken is 0 characters". Nothing to do; the
  troubleshooting guide lists what each message means.
- **browser-rpc prewarms in the background, through `DTK_BROWSER_SIGN_PROXY_URL`**
  when that is set, and retries a platform whose page could not open - after 30
  seconds, doubling up to 10 minutes. A signing proxy that was down at start needs
  no restart once it is back, and `/rpc/health` and minting answer at once instead
  of waiting for prewarm. Nothing to do.
- **The console's parse preview** still tries every link of a video - from some
  networks TikTok's `www.tiktok.com` play link opens - and when none plays, shows
  the cover, says why (a TikTok link tied to its session, or a link the platform
  refused or let expire), and offers **Save to this instance**. For a TikTok
  video, and after a refused preview, the video's save button saves to the
  instance too. Nothing to do.

### Fixed

- **Every TikTok video download failed with 403** on every mirror, or with
  `www.tiktok.com answered content-type "text/html…", which is not a media type
  this job accepts` (#831). TikTok's CDN wants the device id
  and the `tt_chain_token` of the session that fetched the link; dtk now sends
  the identity's own device id, and a TikTok download re-reads the post and hands
  the downloader that identity's `tt_chain_token` - the only cookie it accepts,
  sent only to TikTok's hosts, never written to disk or shown in the job list.
  Thanks to [@yc-cheung](https://github.com/yc-cheung).
- **TikTok identities started failing with `signature.rejected` after about
  twenty requests** and never recovered, until the pool ran out (#830). The token
  TikTok reissues on most answers - the refusal included - is now kept; one
  identity answered 32 author profiles in a row in testing. Thanks to
  [@huynk-04](https://github.com/huynk-04).
- **browser-rpc failed TikTok signatures with `Invalid base URL`** (#830): a
  warm page that a failed navigation had carried off tiktok.com was still handed
  out, and every signature taken from it failed. Such a page is now never handed
  out; it is closed and opened again.
- **`dtk diagnose` reported a browser that could not sign** as "the browser
  reference implements a different version of the algorithm" (#830). It is
  `signing_reference_failed` now, with the browser's own reason.
- **A download that re-read an expired post still used the expired links.**
  The fresh links are used now.
- **The browser and transcriber images could not read their own code** when
  built from a checkout made under `umask 077` or `027` (#827). Thanks to
  [@name-XueWen](https://github.com/name-XueWen).
- **A signing page that failed to open left its profile directory behind** on
  browser-rpc's `/profiles` tmpfs, which is memory; so did one whose open timed
  out, with its Chromium still running. Both are cleaned up now.
- **Pasting links the console parsed a moment ago showed empty rows** marked
  done, and their preview would not open. They show their results now.
- **The build-proxy example in the installation guide** passed only upper-case
  `HTTP_PROXY`/`HTTPS_PROXY`, and apt reads only lower-case `http_proxy`, so apt
  went direct. If you followed it, pass both spellings.

### Security

- **The downloader now receives one cookie.** It used to receive none. A TikTok
  download hands it the fetching identity's `tt_chain_token` and nothing else:
  any other name is refused, it is sent only to TikTok's hosts, it is not written
  to disk, and it never appears in the job list or the logs. The identity's
  other cookies never reach the downloader.
- **Prewarm no longer bypasses `DTK_BROWSER_SIGN_PROXY_URL`.** It used to open
  both platforms' pages from the host's own address at every start, whatever the
  signing proxy said; it now goes out through the proxy when one is set.
- **A PyPI mirror is trusted with the code it serves**: the dependencies are
  pinned by version, not by hash, so only `https://` is accepted. Use a mirror
  you would trust with the build. Debian's packages are signature-checked
  whichever mirror serves them.

---

## 本次更新

**TikTok 视频重新能下载了，TikTok 身份也不会再用二十来次就失效。** 以前 dtk 给出的 TikTok 视频链接，每个镜像都返回 403：TikTok 把视频链接绑定在取到它的那个会话的 device id 上，而 dtk 每次请求都随机编一个 device id。另一个问题是，每个 TikTok `msToken` 都有使用次数上限，dtk 却一直重放身份铸造时拿到的那个，直到 TikTok 永久拒绝它——这就是 #830 里耗尽身份池的那一串 `signature.rejected`。现在身份会保存平台种下的 Cookie，TikTok 身份也会发送它自己的 device id。另外，解析结果会写明直接取媒体链接需要什么，浏览器签名自检会说清楚真正出了什么问题，浏览器镜像和转写镜像可以通过软件源镜像构建。两个平台的 Web 签名都对照线上站点检查过：抖音不需要改，TikTok 现在标注的是 tiktok.com 当前使用的 SDK 版本。

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

它会 checkout `v5.1.12`、拉取 5.1.12、备份数据库、迁移、重启，并自己把 `DTK_IMAGE_TAG=5.1.12` 写进 `.env`，
不需要手改任何东西。

**浏览器镜像会从头重新构建一次。** 系统软件包之前新增了一个步骤，所以构建会重新下载 Chromium：需要几分钟，配置低的机器会更久。CloakBrowser 的 pin 没有变化。如果构建机访问 Debian 或 PyPI 很慢，先设置好 `APT_MIRROR` 和 `PYPI_MIRROR`（见「新增」）。

手工升级：

```bash
cd /opt/dtk && git pull
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
# DTK_IMAGE_TAG=latest 的不用改；钉了版本的改成 5.1.12 —— 不带 v，见文末标签表
docker compose -p dtk -f docker/compose.yml pull api worker downloader transcriber
docker compose -p dtk -f docker/compose.yml build browser-rpc   # 开了 --profile browser 时
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

没有新的迁移，没有新的运行时设置，API Key 也不用动。数据不受影响。

### 新增

- **每个解析结果都带上了 `media.access`**，写明直接取这些链接需要什么：`referer` 是 CDN 要求的 Referer（抖音的 `douyinvod.com` 不带 `https://www.douyin.com/` 就返回 403）；`session_bound: true` 表示这是 TikTok 视频，它的链接只有取到它的那个会话才能打开——这类链接请通过 `/api/v1/downloads` 下载。只有自己直接取媒体链接时才需要关心（#805、#829）。感谢 [@qigaowei](https://github.com/qigaowei)、[@815074104](https://github.com/815074104) 和 [@bling-yshs](https://github.com/bling-yshs)。
- **`.env` 里新增 `APT_MIRROR` 和 `PYPI_MIRROR`**，给访问 Debian 或 PyPI 很慢、甚至访问不了的构建机用：两个都会传进浏览器镜像的构建，本地构建转写镜像时只用 `PYPI_MIRROR`。默认为空；值填错时构建会直接停下，而不是悄悄改用官方源。设置好之后重新构建（`./dtkctl build browser-rpc`）。PyPI 镜像被信任到什么程度，见「安全」（#817）。感谢 [@duiaic](https://github.com/duiaic)。

### 变更

- **身份会保存平台种下的 Cookie。** 响应里种下的 Cookie——包括拒绝的响应，但不包括 5xx 和连接中断——会写回身份，下一次请求就会带上，和浏览器的行为一样。以下几种不收：短于 144 个字符的 TikTok `msToken`、不合法的 Cookie 值、jar 超过 8 KB 后新出现的 Cookie，以及浏览器签名页封进请求里的 `msToken`。worker 日志（`identity.cookies.rotated`）只记录名字，从不记录值。不需要做任何事。
- **TikTok 身份第一次被使用时，会以它自己的身份打开一次 TikTok 首页，读出 device id。** 每个身份多一次请求；页面里没读到的话，一小时后再试。不需要做任何事。
- **TikTok 签名标注的是 webmssdk 2.0.0.569**，也就是 tiktok.com 现在使用的版本（原来是 2.0.0.561）。TikTok 目前不校验这个版本，所以此前并没有因此失败，但旧版本容易被识别出来。不需要做任何事。
- **下载失败时会列出每个镜像的失败原因**，形如 `all 12 mirrors failed: v16-webapp-prime.us.tiktok.com answered 403 (11 mirrors); ...`，而不再只报最后一个镜像的。如果你按文字匹配下载错误，也要匹配这种格式。
- **`dtk diagnose` 会写明签名检查发现了什么。** 新增几个代码：`signing_reference_failed`（警告：浏览器没有产出参考签名，所以什么都没有比对，原生签名不受影响）、`signing_native_failed`（失败）和 `signing_build_drift`（警告：浏览器签名用的是另一个 TikTok SDK 版本）。TikTok 的比对改为比较 `X-Dynosaur` 里的版本信息，恒为常量的 `X-Bogus=1` 不再算作一致。以前所有平台的比对都崩溃时这一步会报通过，现在会报 `signing_not_comparable` 警告。这里新出现的警告，除非文字里写明了要做什么，否则不需要处理。
- **browser-rpc 会说明失败指向哪里。** 签名页离开平台时，会写明它去了哪里（只写协议和主机）；崩溃时会直接说崩溃；两次尝试以同样方式失败时，会指出它们共用的那一环——出口、平台脚本或浏览器。dtk 会转述 browser-rpc 给出的原因，而不是一律报「unreachable」；超时则显示为 `did not answer within Ns`。铸造时如果页面离开了平台，会立即失败并写明它去了哪里，而不是等 18 秒后报「msToken is 0 characters」。不需要做任何事，每条消息的含义见故障排查文档。
- **browser-rpc 改为在后台预热，设置了 `DTK_BROWSER_SIGN_PROXY_URL` 时经由它**，某个平台的页面没能打开就重试：先等 30 秒，之后间隔逐次翻倍，最长 10 分钟。启动时签名代理不可用，等它恢复后也不需要重启；`/rpc/health` 和铸造也会立即响应，不再等预热完成。不需要做任何事。
- **控制台的解析预览**仍然会尝试视频的每一个链接——从某些网络访问时，TikTok 的 `www.tiktok.com` 播放链接能打开；如果都播不了，会显示封面、说明原因（TikTok 链接绑定在会话上，或者链接被平台拒绝、已经过期），并提供「保存到本实例」。对 TikTok 视频，以及预览被拒绝之后，视频的保存按钮也会改为保存到本实例。不需要做任何事。

### 修复

- **TikTok 视频下载全部失败**：每个镜像都返回 403，或者报 `www.tiktok.com answered content-type "text/html…", which is not a media type this job accepts`（#831）。TikTok 的 CDN 要求请求带上取到链接的那个会话的 device id 和 `tt_chain_token`。现在 dtk 会发送身份自己的 device id；下载 TikTok 作品时会重新读取作品，并把这个身份的 `tt_chain_token` 交给下载器——这是下载器唯一接受的 Cookie，只发往 TikTok 的主机，不写盘，也不会出现在任务列表里。感谢 [@yc-cheung](https://github.com/yc-cheung)。
- **TikTok 身份大约用二十次后开始报 `signature.rejected`**，而且再也恢复不了，直到身份池耗尽（#830）。TikTok 在大多数响应里（包括拒绝的响应）都会重新签发 token，现在这个 token 会被保存下来；测试中同一个身份连续请求作者资料 32 次全部成功。感谢 [@huynk-04](https://github.com/huynk-04)。
- **browser-rpc 签 TikTok 时报 `Invalid base URL`**（#830）：一次失败的跳转把常驻签名页带离了 tiktok.com，这个页面却仍被交出去使用，从它取的每次签名都会失败。现在这样的页面不会再被交出去，而是关闭后重新打开。
- **`dtk diagnose` 把浏览器无法签名报成了**「浏览器参考实现的是另一个版本的算法」（#830）。现在报的是 `signing_reference_failed`，并附上浏览器自己给出的原因。
- **下载时重新读取了过期的作品，用的却还是过期的链接。** 现在会用新取到的链接。
- **用 `umask 077` 或 `027` 下 checkout 的代码构建时，浏览器镜像和转写镜像读不了自己的代码**（#827）。感谢 [@name-XueWen](https://github.com/name-XueWen)。
- **签名页打开失败时会把 profile 目录留在** browser-rpc 的 `/profiles` tmpfs 上，占用的是内存；打开超时的页面也一样，而且它的 Chromium 还在运行。现在两种情况都会清理干净。
- **在控制台粘贴刚解析过的链接时，行显示为已完成但内容为空**，预览也打不开。现在会正常显示结果。
- **安装文档里的构建代理示例**只传了大写的 `HTTP_PROXY`/`HTTPS_PROXY`，而 apt 只认小写的 `http_proxy`，所以 apt 那一步其实是直连的。照着做过的话，大小写两套都传上。

### 安全

- **下载器现在会收到一个 Cookie。** 以前它一个都不收。下载 TikTok 作品时，会把取详情那个身份的 `tt_chain_token` 交给它，仅此一个：其他名字一律拒收，只发往 TikTok 的主机，不写盘，也从不出现在任务列表和日志里。身份的其他 Cookie 不会交给下载器。
- **预热不再绕过 `DTK_BROWSER_SIGN_PROXY_URL`。** 以前每次启动都用本机地址打开两个平台的页面，不管签名代理怎么设置；现在设置了代理就经由它。
- **PyPI 镜像会被信任到它提供的代码**：依赖只锁了版本、没有锁哈希，所以只接受 `https://`。请用你愿意交给它构建的镜像。Debian 的软件包无论哪个镜像提供都会校验签名。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.12` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.12` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-a0167cc2da46b942f9048337c962ed61b2dd8ad4` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.12` was never pushed. One value names all three published images.
`latest` and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.12`
这个标签从来没有被推送过。同一个值同时对应三个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader` · `evil0ctal/douyin_tiktok_download_api-transcriber`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.11...v5.1.12
