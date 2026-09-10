# Release notes template

Copy this into the GitHub release body and fill it in. English first, then
Chinese, because the file is read by both and one order has to be picked.

Rules that keep these useful rather than ceremonial:

* **Lead with what changed for the reader, not with what was committed.** "The
  demo account can now try the API" beats "add `demo_read` guard".
* **Breaking changes and upgrade steps go at the top**, above the feature list.
  Somebody skimming on a phone at 2am should hit them first.
* **Every entry says what to do**, or says nothing needs doing. A line that only
  names a change makes the reader open the diff.
* **No "various bug fixes".** Either it is worth a line or it belongs to the
  commit log, which is linked at the bottom anyway.
* Keep the two languages in step. Same sections, same entries, same order — a
  reader comparing them should never wonder which one is current.

Delete this header block before publishing.

---

## What's new

<!-- One paragraph. What this release is for, in the words of somebody using it. -->

### Upgrading

<!-- Delete if a plain pull and restart is enough; say so explicitly if it is. -->

```bash
cd /opt/dtk && git pull
# set DTK_IMAGE_TAG=vX.Y.Z in .env
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Your data is untouched: the named volumes survive a rebuild, so the identity
pool, the archive, the settings and the API keys stay where they are.

### Breaking changes

<!-- Delete the section if there are none. Never leave it saying "none" in one
     language and listing something in the other. -->

- **`setting.name` changed meaning.** Was X, is now Y. If you set it, do Z.

### Added

- **Short title.** What it does, and what you do to use it.

### Changed

- **Short title.** What is different, and whether you need to act.

### Fixed

- **Short title.** What went wrong, who it affected, what it does now.

### Security

<!-- Delete if empty. Anything touching auth, scopes, credentials or exposure
     goes here even when it is also in Fixed. -->

---

## 本次更新

<!-- 一段话。这个版本是为了什么，用使用者的话说。 -->

### 升级方式

<!-- 如果拉一下重启就够，删掉代码块并明说「拉一下重启即可」。 -->

```bash
cd /opt/dtk && git pull
# 在 .env 里把 DTK_IMAGE_TAG 改成 vX.Y.Z
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

数据不受影响：命名卷不随容器重建而消失，身份池、归档、设置和 API Key 都在原地。

### 不兼容变更

<!-- 没有就整节删掉。不要出现一种语言写「无」而另一种语言列了内容的情况。 -->

- **`setting.name` 含义变了。** 原来是 X，现在是 Y。如果你设过它，请做 Z。

### 新增

- **一句话标题。** 它做什么，你要怎么用上它。

### 变更

- **一句话标题。** 有什么不同，你需不需要动手。

### 修复

- **一句话标题。** 原来出了什么问题、影响谁、现在的行为是什么。

### 安全

<!-- 没有就删掉。凡是涉及认证、权限范围、凭据或暴露面的，即使已经写在「修复」里，
     也要在这里再列一次。 -->

---

**Full changelog / 完整提交记录**: https://github.com/Evil0ctal/Douyin_TikTok_Download_API/compare/vPREV...vTHIS

**Docker**: `evil0ctal/douyin_tiktok_download_api:vX.Y.Z`
