<!--
GitHub has no template chooser for pull requests the way it has for issues, so
this one is bilingual and it is what everybody gets. If you would rather have a
single language, append one of these to the compare URL before you open the PR:

  ?template=en.md     English only
  ?template=zh.md     只有中文

Delete whichever half you did not fill in. Delete these comments too.
删掉你没填的那一半，也删掉这些注释。
-->

## What changed / 改了什么

<!-- Behaviour, not files. "A post that does not exist is an answer, not risk
     control" beats "edited fetch.py". -->
<!-- 写行为，不要写动了哪些文件。 -->

## Why / 为什么

<!-- The problem this solves. Link the issue if there is one: Fixes #123 -->
<!-- 它解决了什么问题。有 issue 就关联：Fixes #123 -->

## How you verified it / 你怎么验证的

<!-- Commands you ran, what you saw. If something could not be verified - a code
     path needing the live platform, a browser backend you do not have - say so.
     An honest "not verified against a real browser" is worth more than a
     confident claim a reviewer has to disprove. -->
<!-- 跑了什么命令、看到了什么。有验证不了的地方就直说：比如需要真实平台才能走到的分支、
     你本地没装的浏览器后端。老实说"没在真浏览器上验证过"，比一个需要 reviewer 去推翻的
     肯定说法有价值得多。 -->

## Checklist / 自查

- [ ] `make fmt` — 格式化先跑，免得 CI 挂在空白字符上 / run first, so the gate does not fail on whitespace
- [ ] `make lint && make type` — ruff and mypy
- [ ] `make test` — unit, replay and integration
- [ ] `(cd web && npm run verify)` — 动过 `web/` 才需要 / only if you touched `web/`
- [ ] 文档 `documents/en/` **和** `documents/zh/` 一起更新了 / user documentation updated in **both**, if a reader would act on the change (a setting, a CLI command, an endpoint, a console page, a default)
- [ ] 没有：源码里的中日韩字符、tokens 之外的十六进制颜色、JSX 里的字面文案、`TODO`、`.env`、带真实 Cookie 的 fixture / none of: CJK in source, a hex colour outside the tokens file, literal prose in JSX, a `TODO`, a `.env`, a fixture with a real cookie
- [ ] 没有在 diff 或描述里贴出密钥、口令、Cookie 或 API Key / no key, password, cookie or API key in the diff or in this description

<!--
PR 目标分支是 main。v4 分支已冻结，只接受安全修复。
Pull requests target main. The v4 branch is frozen and takes security fixes only.

新增平台接口 / 控制台页面 / 翻译字符串，各有一节现成的步骤：
Adding a platform endpoint, a console page or a translated string each has its own
section in: documents/en/16-contributing.md
-->
