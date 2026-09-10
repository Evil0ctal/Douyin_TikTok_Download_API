# Contributing / 参与开发

**The full guide lives in the documentation, in both languages.** This file is
what GitHub links to from the issue and pull-request pages, so it is deliberately
short — everything below is a pointer.

**完整指南在文档里，中英双语。** 这个文件是 GitHub 在 issue 和 PR 页面上链接的那一个，
所以刻意写得短，下面全是指路。

| | |
|---|---|
| 📖 Full guide | [documents/en/16-contributing.md](./documents/en/16-contributing.md) |
| 📖 完整指南 | [documents/zh/16-contributing.md](./documents/zh/16-contributing.md) |

---

## Before you start

- **Open an issue first** for anything larger than a bug fix. Not as a formality —
  it is how you find out that something already does it, or that a design decision
  went the other way on purpose.
- **Read the code of conduct**: [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
- **Security problems do not go in a public issue.** See [SECURITY.md](./SECURITY.md).
- **v5 is `main`.** v4 lives on the [`v4` branch](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4)
  and is frozen except for security fixes.

## The short version

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
make install                    # sync the dev environment with uv
make fixtures-up                # PostgreSQL + Redis for the integration tests
make test                       # unit, replay and integration
```

Before opening a pull request:

```bash
make fmt && make lint && make type
make test
(cd web && npm run verify)      # only if you touched web/
```

## Three things that catch people out

1. **Source is English.** Every user-visible string comes from a translation
   catalogue, and a test fails on CJK characters in Python or TypeScript source.
   If you do not write Chinese, machine-translate the `zh` strings, say so in the
   pull request, and they will be rewritten by hand — that is much easier to deal
   with than a missing key, which falls back to English silently.
2. **Documentation moves in both languages.** `documents/en/` and `documents/zh/`
   are the same document; changing one without the other is what makes a
   translation rot.
3. **No credentials, ever.** Not in a fixture, not in a `.env`, not in an issue,
   not in a pull-request description. A fixture with a real cookie in it fails a
   test, and that test exists because v4 shipped a live session in a tracked
   config file.

The full guide covers the repository layout, the test layers, the quality gates,
and step-by-step recipes for **adding a platform endpoint**, **adding a console
page**, **adding a setting**, **adding a translated string** and **adding an error
code**.

---

## 开工之前

- **比修 bug 大的改动，先开一个 issue。** 不是走流程 —— 这是你发现"现有的东西已经能做到"
  或者"当初就是刻意不这么设计"的最快方式。
- **看一眼行为准则**：[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。
- **安全问题不要开公开 issue**，见 [SECURITY.md](./SECURITY.md)。
- **v5 就是 `main`。** v4 在 [`v4` 分支](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4)，
  已冻结，只接受安全修复。

## 最短路径

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
make install                    # 用 uv 同步开发环境
make fixtures-up                # 集成测试要用的 PostgreSQL + Redis
make test                       # 单元、回放、集成
```

开 PR 之前：

```bash
make fmt && make lint && make type
make test
(cd web && npm run verify)      # 动过 web/ 才需要
```

## 三件最容易踩的事

1. **源码是英文的。** 每一条用户可见的文案都来自翻译目录，Python 和 TypeScript 源码里出现
   中日韩字符会有测试直接失败。不写中文没关系：中文字符串机翻一下、在 PR 里说明，
   我来手工重写 —— 这比缺 key 好处理得多，缺 key 会静默回退英文，没人会发现。
2. **文档要两种语言一起动。** `documents/en/` 和 `documents/zh/` 是同一份文档，
   只改一边，翻译就是从这里开始烂掉的。
3. **任何时候都不要贴凭据。** fixture 里不行、`.env` 里不行、issue 里不行、PR 描述里也不行。
   带真实 Cookie 的 fixture 会让测试失败 —— 那条测试之所以存在，是因为 v4 曾经把一个
   活着的登录态提交进了配置文件。

完整指南里有仓库结构、测试分层、质量门禁，以及**新增平台接口**、**新增控制台页面**、
**新增配置项**、**新增翻译字符串**、**新增错误码**五套逐步骤的做法。
