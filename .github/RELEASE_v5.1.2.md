## What's new

**A security release: a caller-supplied `?proxy=` could reach the instance's own
network.** On instances where an administrator had set `security.request_proxy`
to `public`, the check that keeps a caller's proxy on public addresses only read
the hostname and never looked it up, so a name that resolves to an internal
address got through
([GHSA-q3h8-73xx-gwqx](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/security/advisories/GHSA-q3h8-73xx-gwqx)).
The proxy's name is now resolved, every address it answers with must be public,
and the request goes to the address that was checked. If you never changed
`security.request_proxy` from its default `deny`, you were not affected. The
repository also gains a `NOTICE` file
([#766](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/766)).

### Upgrading

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=5.1.2 in .env — no `v`, see the tag table at the bottom
export COMPOSE_ENV_FILES=.env   # without this, `image:` ignores the root .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Or run the installer again and pick Upgrade:

```bash
bash install/install.sh --manage
```

Your data is untouched. No new migrations in this release. If you run with
`security.request_proxy` set to `public` and cannot upgrade right away, set it
to `deny` until you can.

### Added

- **A `NOTICE` file with the project's copyright notice.** The README asked
  anyone redistributing the project to keep "the copyright notice", and the
  repository did not have one. Nothing to do; if you redistribute the project,
  keep `NOTICE` next to `LICENSE`.

### Changed

- **In `public` mode, a `?proxy=` given by name is resolved and pinned.** Every
  address the name resolves to must be public, and the request is sent to the
  address that was checked rather than to the name. An `https` proxy keeps its
  name, because its certificate is checked against it. Two new refusal reasons
  come with this: `host_unresolvable` for a name that does not resolve within
  five seconds, and `authority_invalid` for a host, port or credentials
  containing characters a URL does not allow there. If your proxy's username or
  password contains such characters, percent-encode them. `any` mode is
  unchanged.
- **Hostnames that spell a private address with hyphens are refused
  everywhere hosts are checked.** A name like `app-10-0-0-1.example.com` now
  counts as private for task callbacks, notification URLs and the URL allowlist,
  just as `10.0.0.1` itself always did. A name spelling a public address is
  unaffected. Nothing to do unless one of your callback or notification URLs has
  such a name.

### Security

- **SSRF through `?proxy=` in `public` mode (GHSA-q3h8-73xx-gwqx).** Affects
  5.0.0 through 5.1.1, only when `security.request_proxy` is `public`. Anyone
  holding an API key with read access could name a proxy whose hostname
  resolves to loopback, a private range or a cloud metadata address, and the
  worker would connect to it. The hostname is now resolved and every answer
  checked before the request is accepted, and what is passed on is the checked
  address, so it cannot be looked up again and answer differently. Upgrade; until
  then, set `security.request_proxy` to `deny`. Instances on `deny` (the default)
  or `any` are not affected by this change. Reported by
  [@xiao1212998](https://github.com/xiao1212998) — thank you.

---

## 本次更新

**这是一个安全更新：调用方传入的 `?proxy=` 可以访问到实例自己所在的网络。** 在管理员把
`security.request_proxy` 设成 `public` 的实例上，用来保证调用方代理只能是公网地址的检查
只看主机名的字面、从不解析它，所以一个解析到内网地址的域名能直接通过
（[GHSA-q3h8-73xx-gwqx](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/security/advisories/GHSA-q3h8-73xx-gwqx)）。
现在会先解析代理的域名，它解析出的每一个地址都必须是公网地址，请求发往的是通过检查的那个
地址。如果你从没把 `security.request_proxy` 从默认的 `deny` 改掉，你不受影响。仓库里还新增了
一个 `NOTICE` 文件
（[#766](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues/766)）。

### 升级方式

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 5.1.2 —— 不带 v，见文末标签表
export COMPOSE_ENV_FILES=.env   # 不加这句，image: 的插值读不到根目录的 .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

或者重新运行安装脚本并选择「升级」：

```bash
bash install/install.sh --manage
```

数据不受影响。这个版本没有新增数据库迁移。如果你的 `security.request_proxy` 是 `public`
而又没法马上升级，请先把它改成 `deny`，升级之后再改回来。

### 新增

- **新增 `NOTICE` 文件，写明项目的版权声明。** README 要求分发本项目的人「保留版权声明」，
  而仓库里之前根本没有版权声明。不用做任何事；如果你要分发本项目，请把 `NOTICE` 和
  `LICENSE` 放在一起。

### 变更

- **`public` 模式下，以域名给出的 `?proxy=` 会被解析并钉住地址。** 域名解析出的每一个地址都
  必须是公网地址，请求发往的是通过检查的那个地址而不是域名。`https` 代理保留域名，因为它的
  证书要按域名校验。随之新增两个拒绝原因：`host_unresolvable` 表示域名在五秒内没有解析出
  结果，`authority_invalid` 表示主机、端口或凭据里有 URL 不允许出现在那里的字符。如果你代理
  的用户名或密码里有这样的字符，请对它们做百分号编码。`any` 模式不变。
- **用连字符拼出私有地址的主机名，在所有检查主机的地方都会被拒绝。** 像
  `app-10-0-0-1.example.com` 这样的名字，现在在任务回调、通知 URL 和 URL 白名单里都算作私有
  地址，和 `10.0.0.1` 本身一直以来的待遇一样。拼出公网地址的名字不受影响。除非你的回调或通知
  URL 用了这种名字，否则不用做任何事。

### 安全

- **`public` 模式下通过 `?proxy=` 的 SSRF（GHSA-q3h8-73xx-gwqx）。** 影响 5.0.0 到 5.1.1，
  且仅限 `security.request_proxy` 为 `public` 的实例。任何持有读权限 API Key 的人都可以指定
  一个主机名解析到回环、私有网段或云元数据地址的代理，worker 就会去连接它。现在请求被接受之前
  会先解析主机名并检查每一个解析结果，往下传的是检查过的地址，不会被再次解析出不同的答案。
  请升级；升级之前请把 `security.request_proxy` 设为 `deny`。使用 `deny`（默认）或 `any` 的
  实例不受这项变更影响。感谢 [@xiao1212998](https://github.com/xiao1212998) 的报告。

---

### Tags / 标签

|  |  |
|---|---|
| Git tag / Git 标签 | `v5.1.2` |
| `DTK_IMAGE_TAG` — this release / 这个版本 | `5.1.2` |
| `DTK_IMAGE_TAG` — this exact build / 钉死这次构建 | `sha-81cabb5c6f0f9420857b85076508c2be03259e9c` |

The Docker tag drops the `v`: `docker/metadata-action` strips it when publishing,
so `:v5.1.2` was never pushed. One value names both published images. `latest`
and `5.1` move; pin `sha-` in production.

Docker 标签不带 `v`：`docker/metadata-action` 在发布时会把它剥掉，所以 `:v5.1.2`
这个标签从来没有被推送过。同一个值同时对应两个已发布镜像。`latest` 和 `5.1` 会移动，
生产环境请钉 `sha-`。

**Images / 镜像**: `evil0ctal/douyin_tiktok_download_api` · `evil0ctal/douyin_tiktok_download_api-downloader`

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/v5.1.1...v5.1.2
