# 参与开发

这一页写给想改代码的人。读完你应该能搭好开发环境、认得清目录结构、在本地跑起 API、worker 和控制台，跑通测试套件的每一层，并让一次改动通过 CI 的全部门禁。

## 你需要什么

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| Python | `>=3.12,<3.14` | `pyproject.toml` 里的约束。容器镜像基于 3.12.8 构建（`docker/compose.yml` 里的 `PYTHON_VERSION`）。 |
| uv | 当前版本 | 唯一受支持的安装与运行方式。`uv.lock` 是锁定文件，CI 带着 `UV_FROZEN=1` 跑。 |
| Node.js | 22 | 控制台。CI 的 console job 用的就是这个版本。 |
| Docker + Compose v2 | 当前版本 | 集成测试用的 PostgreSQL 和 Redis，以及完整技术栈。 |
| Go | 1.23 | 只有在动 `docker/downloader/` 里的媒体下载器时才需要。 |

仓库根目录没有 `requirements.txt`，也没有 `setup.py`（browser-rpc sidecar 单独打镜像，自己带一份 `requirements.txt`）。依赖声明在 `pyproject.toml`，解析结果落在 `uv.lock`；改依赖意味着两个文件都要改并且把锁文件一起提交，否则 CI 会在 `uv sync` 阶段就失败，而不是在你的代码上失败。

开发不需要无头浏览器。`browser-rpc` 是一个可选的 compose profile，签名默认走进程内的 `native` 实现，没有配置浏览器服务时身份池会退回到手动导入 Cookie。见[身份与代理](./06-identities-and-proxies.md)。

## 准备环境

v5 就在 `main` 上，clone 下来默认就是它。

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

仓库有三个分支，各自的角色不同：

| 分支 | 是什么 | 会不会收 PR |
|---|---|---|
| `main` | v5，开发主线 | 会——所有 PR 都提到这里 |
| `release` | 已发布为 Docker 镜像的那个提交 | 不会，它只从 `main` 快进 |
| `v4` | 旧代码，已冻结 | 收，但只收安全和阻塞性修复 |

`main` 和 `v4` 之间没有共同祖先——v5 是从空分支重写的，所以两者之间不存在 merge 或 cherry-pick 这回事。

Python 这一侧：

```bash
uv sync --all-extras
```

`make install` 就是这条命令。`--all-extras` 会装上 `dev` 这组额外依赖——pytest、pytest-asyncio、pytest-cov、ruff、mypy、types-redis——除此之外没有别的地方会装它们。

控制台这一侧：

```bash
(cd web && npm ci)
```

用 `npm ci` 而不是 `npm install`：它严格按照 `package-lock.json` 安装，和 CI 的行为一致，不会在你不知情的时候把某个版本挪走。

确认两边都能跑：

```bash
uv run dtk --version
(cd web && npx tsc --version)
```

下面所有命令，除非另有说明，都在仓库根目录执行。

## 仓库结构

顶层：

| 路径 | 放什么 |
| --- | --- |
| `src/dtk/` | Python 应用。一个可安装包 `dtk`，在 `pyproject.toml` 里声明为 `packages = ["src/dtk"]`。 |
| `tests/` | Python 测试套件，分四层——见[测试套件](#测试套件)。 |
| `web/` | React 控制台。构建成静态文件由 API 容器托管，生产环境没有 Node。 |
| `docker/` | 镜像、compose 文件，以及两个不在 `dtk` 包里的可选 sidecar：`browser_rpc/`（Python，FastAPI + 无头浏览器，自带一套依赖）和 `downloader/`（Go）。 |
| `scripts/` | `smoke.sh`，整栈端到端检查。 |
| `documents/` | 你正在读的这套文档，分 `en/` 和 `zh/`。 |
| `logo/` | 项目标识，SVG 和 PNG。 |
| `Makefile` | 本页所有命令的简写。某条命令的行为让你意外时就去读它——它才是源头。 |
| `alembic.ini` | 迁移配置。刻意不写数据库 URL，`env.py` 从 `DTK_DATABASE_URL` 读。 |
| `.env.example` | 引导阶段的环境变量；三个密钥变量留空，其余每一项都带着它自己说明的那个默认值。你自己的 `.env` 已被 git 忽略，也必须一直如此。 |

有一条路径你会反复读到、却在仓库里找不到：`src/`、`web/`、`tests/`、`docker/` 下大约 160 个文件的 docstring 和注释里都引用了 `docs/design/NN-*.md`，`.github/workflows/ci.yml` 里也有。`.gitignore` 忽略了整个 `docs/`，所以这个目录不随仓库分发，你 clone 下来是没有它的。那些是作者自己的设计笔记；对外发布的等价物就是 `documents/`，只是它按读者的顺序组织，并不和那边一一对应。docstring 里写着 `docs/design/13-testing.md`，那不是一个待修的坏链接，而是作者留给自己的备注。

`src/dtk/` 内部：

| 包 | 放什么 |
| --- | --- |
| `api/` | FastAPI：应用工厂、中间件、响应信封、依赖，以及 `routes/`（`routes/admin/` 放仅控制台可用的路由）。`console.py` 负责托管构建好的 SPA。 |
| `cli/` | `dtk` 命令。每个命令组一个模块；`runtime.py` 管一次调用的完整生命周期。 |
| `core/` | 配置、加密、引擎与会话、Redis、日志、错误码、共享枚举。所有人都可以 import 它，它谁也不 import。 |
| `db/` | ORM 模型、仓储、`migrations/versions/` 下的 Alembic 版本、TimescaleDB 相关辅助。 |
| `i18n/` | 后端词条表与本地化格式化。中文只放在 `locales/*.json`，绝不出现在 Python 里。 |
| `identity/` | 身份池、Cookie 导入、铸造日志、browser-rpc 客户端。 |
| `mcp/` | MCP 服务器。它直接调 service 层，不去调本项目自己的 HTTP API。 |
| `media/` | Go 下载器 sidecar 的客户端、CDN 域名白名单、下载计划。 |
| `models/` | 两个平台归一化之后的领域模型（`Content`、`Author`、`Comment`、`Page`）。 |
| `ops/` | 健康检查、备份、通知、诊断、保留策略、脱敏、webhook——自托管者真正需要、而任何架构图都不画的那些东西。 |
| `platforms/` | 每个平台一个子包。接口表、参数构造器、解析器。**不做任何 IO。** |
| `scheduler/` | 排序、租约、令牌桶、熔断器、按接口的调度策略。 |
| `services/` | REST、MCP 和 CLI 共用的那一层：fetch、archive、downloads、tasks、watchlist、collections、cache、settings。 |
| `signing/` | 签名算法（`native/`）、浏览器兜底（`rpc.py`），以及在两者间做选择的注册表。 |
| `transport/` | wreq 客户端、TLS/浏览器仿真、请求头、响应分类。这一层之上的任何包都不 import wreq。 |
| `urls/` | URL 识别、ID 提取、短链展开。同时是 SSRF 的唯一收口点——别的地方不要手写平台 URL 解析。 |
| `worker/` | 任务循环、各个后台循环（补池、代理探测、维护、监控），一次性操作放在 `ops/`，以及 `registry.py`——唯一定义 `douyin.content_detail` 是什么的那张表。 |

`web/src/` 内部：

| 路径 | 放什么 |
| --- | --- |
| `main.tsx` | 主题与 i18n 引导、根渲染、文档标题。 |
| `App.tsx` | 外壳、路由、首次初始化拦截。 |
| `pages/` | 一个路由一个文件。 |
| `components/` | 设计系统的组件清单。从 `@/components` 导入。 |
| `hooks/` | 共享 hook。 |
| `lib/` | API 客户端、接口路径、格式化、i18n、主题、导航。 |
| `locales/{en,zh}/` | `common`、`console`、`errors`、`setup`。中文文案唯一能待的地方。 |
| `styles/` | `tokens.css`、`base.css`、`utilities.css`。颜色字面量唯一能待的地方。 |

`@/` 解析到 `web/src/`。TypeScript 开了 strict，另加 `noUncheckedIndexedAccess` 和 `verbatimModuleSyntax`——下标访问的类型是 `T | undefined`，纯类型导入必须写 `import type`。

## 开发时依赖的服务

生产 compose 文件里的 Postgres 和 Redis 挂在 `internal: true` 的网络上，不发布任何端口，所以宿主机上的进程根本连不上。开发用的是另一个独立的 compose 项目：

```bash
make fixtures-up
```

也就是：

```bash
docker compose -p dtk-test -f docker/compose.test.yml up -d --wait
```

| 服务 | 地址 | 凭据 |
| --- | --- | --- |
| PostgreSQL（`timescale/timescaledb-ha:pg17`） | `127.0.0.1:55432` | 用户 `dtk`，密码 `dtk_test_password`，数据库 `dtk_test` |
| Redis 8 | `127.0.0.1:56379` | 无密码 |

两者都只绑在回环地址上。集成测试从 `DTK_TEST_DATABASE_URL` 和 `DTK_TEST_REDIS_URL` 读这两个地址，缺省值就是上表的值，所以你本机已经跑着 Postgres 和 Redis 的话，把这两个变量指到别处即可。

**这套测试夹具是一次性的。** `make fixtures-down` 执行的是 `down -v`，容器和卷一起删；Redis 也是关掉持久化跑的。你放进去的东西一样都留不下来。这是刻意的：一个会攒状态的测试夹具，迟早会用一个和你的改动毫无关系的失败来解释问题。

把它当开发数据库用之前，有两件事得先知道：

- 集成测试在每个用例前清空所有表并 flush Redis。你如果同时拿这套夹具在开发，跑一次测试就会把你的数据删掉。
- 测试套件把 Redis 的 9 号逻辑库留给 API 测试，避免相邻套件在用例中途 flush 掉一个会话。你自己的进程默认用 0 号。

完整技术栈是另一回事，那是[安装与部署](./02-installation.md)讲的内容：

```bash
make up      # docker compose -p dtk -f docker/compose.yml up -d --wait
make logs
make down    # 停掉整套栈，数据保留
```

用它来验证改动在真实部署里成立，而不是用它做你日常改代码、重载的循环。

**`make clean` 刻意不放进上面那一块，它也不是清夹具的命令。** 它对 `dtk` 这个项目——也就是真实的那套栈——执行 `down -v --remove-orphans`，同时也对测试夹具、以及 `scripts/smoke.sh` 留下的 `dtk-smoke` 项目各执行一遍。所以它删掉的不只是所有 `dtk` 的容器和网络，还有那些卷：`postgres-data`、`redis-data`、`backup-data`、`media-data`。那是你这个实例的数据库、备份和已经下载的媒体，不只是上面那些一次性的夹具。这和[安装与部署](./02-installation.md)里说“不可逆”的是同一条命令——想留下的归档请先从备份卷里拷出来。停掉栈而把数据原样留着的那条是 `make down`。

## 本地运行 API 与 worker

有三个环境变量在数据库可用之前就要读到，其中第一个缺了进程会直接拒绝启动：

```bash
export DTK_SECRET_KEY=$(openssl rand -base64 48)
export DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test
export DTK_REDIS_URL=redis://127.0.0.1:56379/0
```

`DTK_SECRET_KEY` 至少 32 个字符。它加密所有存下来的 Cookie 和代理凭据，所以任何地方都不带默认值——一把在每个安装上都相同的密钥不叫加密。开发时随手生成一把，不要复用生产的那把。

**一个值得点名的坑。** `BootstrapSettings` 除了读环境变量，还会读当前工作目录下的 `.env`。如果你为 compose 栈在仓库根目录写过 `.env`，那里面写的是 `postgres:5432` 和 `redis:6379`——容器主机名，在宿主机上解析不了。真正的环境变量优先级高于文件，所以上面那种 export 的写法才是可靠的。

建好表结构，然后运行：

```bash
uv run dtk migrate
uv run dtk serve --reload
```

`dtk serve` 从引导配置里读 `bind_host` 和 `bind_port`（默认 `127.0.0.1:8000`），`--host` / `--port` 可以覆盖。`--reload` 不能和 `--workers` 一起用；命令会直接告诉你，而不是启动一个让人困惑的东西。

worker 是另一个进程，所有异步的事情——解析、下载、关注列表采集、身份铸造、备份——没有它就是死的：

```bash
uv run dtk worker
```

它刻意不带任何调优参数。并发数和各后台循环的间隔属于进程配置，不属于调用方式，这样你在故障处理时手工起的 worker，行为和 compose 里那个完全一致。它响应 SIGTERM 时会排空：立刻停止认领新任务，正在执行的留出时间做完。

`uv run alembic upgrade head` 和 `dtk migrate` 等价，CI 用的是前者；`alembic.ini` 不写 URL，`env.py` 从环境里读 `DTK_DATABASE_URL`（其次 `DATABASE_URL`），所以带密码的连接串没有机会落进一个会被提交的文件里。

如果 `web/dist` 存在，API 会在 `/` 托管构建好的控制台。如果不存在——在一个 checkout 里这是常态——那些路由就干脆不存在，你改用开发服务器。

## 运行控制台开发服务器

```bash
(cd web && npm run dev)
```

控制台会跑在 `http://localhost:5173`，并把 `/api`、`/docs`、`/redoc`、`/openapi.json`、`/healthz` 和 `/readyz` 代理到 `http://127.0.0.1:8000`——也就是上一节那个 API 进程。除此之外什么都不代理。

| 变量 | 作用 |
| --- | --- |
| `DTK_API_TARGET` | 开发代理把这些前缀转发到哪里。默认 `http://127.0.0.1:8000`。 |
| `VITE_API_BASE_URL` | 只有当控制台和 API 不同源时才需要。 |

其他脚本，都在 `web/` 下执行：

| 命令 | 做什么 |
| --- | --- |
| `npm run dev` | Vite 开发服务器，端口 5173。 |
| `npm run typecheck` | 对 `tsconfig.json` 和 `tsconfig.node.json` 各跑一次 `tsc --noEmit`。 |
| `npm run lint` | ESLint（`--max-warnings 0`），然后 `node scripts/check-i18n.mjs`。 |
| `npm run build` | 先按 `tsconfig.json` 类型检查，再产出 `web/dist`。 |
| `npm run preview` | 预览构建产物。 |
| `npm run verify` | `typecheck`、`lint`、`build`——CI 跑的那三个，按 CI 的顺序。 |

## 测试套件

四层，正是这种切分让每一层都值得信任。配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 里：`asyncio_mode = "auto"`（异步测试不需要装饰器）、`testpaths = ["tests"]`，以及两个声明过的 marker。

| 层 | 路径 | 依赖 | Marker | 运行方式 |
| --- | --- | --- | --- | --- |
| 单元 | `tests/unit/` | 无 | 无 | `uv run pytest tests/unit -q` |
| 回放 | `tests/replay/` | 无 | 无 | `uv run pytest tests/replay -q` |
| 集成 | `tests/integration/` | PostgreSQL + Redis | `integration` | `uv run pytest tests/integration -q -m integration` |
| 契约 | `tests/contract/` | 一个运行中的部署 + 真实平台 | `live` | `uv run pytest tests/contract -m live -v` |

两个 marker 的声明原文是：

- `integration: requires PostgreSQL and Redis`
- `live: hits the real platform; never runs in CI gating`

### 单元与回放

```bash
make test-unit          # uv run pytest tests/unit tests/replay -q
```

不需要服务，不碰网络，不读配置。单元测试覆盖调度器的算术、签名算法、URL 解析、worker 的分类逻辑、ops 各模块、CLI，以及一组仓库卫生检查——见[代码规范](#代码规范)。

回放测试把存下来的响应体直接喂给平台解析器——`dict` 进，模型出，不做 IO——这之所以可能，正是因为解析器是纯函数。它们还断言**归一化一致性**：对同一个逻辑对象，两个平台必须产出相同的字段集合，差别只能在于哪些字段是 `None`。单平台的测试永远发现不了两者何时开始分叉。

`tests/fixtures/<platform>/` 每个平台有十一个 JSON 文件，一种响应形态一个（正常视频、图集、已删除作品、私密作品、长文案、作者资料、作品分页、评论、评论回复，以及两种风控形态）。新增或替换之前请先读 `tests/fixtures/README.md`——脱敏规则写在那里，而且 `tests/replay/test_platform_contract.py` 会因为一个含有凭据形状 key 的 fixture 直接让构建失败。

要知道现在这些 fixture 到底是什么：**它们每一个都是照着 V4 的解析与请求代码手写出来的**，不是从真实平台抓下来的，发版之前应当被真实抓包替换掉。README 里就是这么写的，`test_fixture_readme_states_they_are_hand_built` 把这段提示钉住，免得它在某次编辑里悄悄消失。在真实抓包补上之前，回放测试全绿的含义是“解析器做的事和我们以为平台做的事一致”，而不是“解析器和平台一致”。

### 集成

```bash
make test-integration
```

它会拉起夹具、执行 `uv run pytest tests/integration -q -m integration`，然后无论成败都用 `down -v` 拆掉。

第一次对全新的夹具跑这一层时，先把迁移应用上——CI 有单独一步做这件事，而 `make test-integration` 不会替你做。`tests/integration/test_migrations.py` 断言的是数据库的**物理形态**——超表（hypertable）、分块间隔、策略——这些只有 Alembic 版本脚本才能建出来：

```bash
make fixtures-up
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test \
  uv run alembic upgrade head
uv run pytest tests/integration -q -m integration
```

这一层每个模块都写了 `pytestmark = pytest.mark.integration`，所以 `-m integration` 是过滤而不是必需——但你新加的东西也要带上这个 mark，因为正是它让一次运行可以整层排除掉。Postgres 或 Redis 连不上时，`tests/integration/conftest.py` 里的 fixture 会**跳过**并提示你去跑 `make fixtures-up`，而不是抛一个连接错误。

`tests/integration/test_api_support.py` 放着 API 测试共用的 fixture。它的 `TABLES` 元组按子表在前的顺序列出每个用例前要清空的表，而 `tests/unit/test_repo_hygiene.py` 会在某张表存在于 schema 却不在这个列表里时失败。往模型里加一张表，就往 `TABLES` 里加一行。

### 契约

这一层打的是真实平台，存在的意义是回答一个别处回答不了的问题：**平台变了吗？** 回放测试跑的是仓库里冻住的 fixture——今天是手写的，将来才是真实抓包——所以它会在生产已经坏掉的时候继续通过。

```bash
DTK_CONTRACT_BASE_URL=http://127.0.0.1:8000 \
DTK_CONTRACT_API_KEY=dtk_xxx_yyy \
  uv run pytest tests/contract -m live -v
```

两个变量缺任何一个，所有用例都会跳过，这也是普通 CI 里发生的事。`tests/contract/subjects.py` 是**刻意空着**的：一份全新的 clone 没有测试对象，套件直接跳过，而不是败在别人删掉的一个视频上。

它刻意不作为合并门禁。契约测试失败的原因常常和被评审的这次改动毫无关系——平台抖了、代理掉了、身份池空了——让 PR 卡在这上面，只会教会所有人无视一个红色检查项。在 `.github/workflows/ci.yml` 里这个 job 带着 `continue-on-error: true`，并被 `if: github.event_name == 'schedule'` 挡着——但那个 workflow 只声明了 `push` 和 `pull_request` 两个触发器，所以这个条件永远不成立，这个 job 在 CI 里根本不会运行。今天它是一套手工跑的测试；要让它真的跑起来，得给 workflow 补上 `schedule:` 触发器。

### 两个不在 `tests/` 下的套件

`testpaths = ["tests"]` 意味着直接跑 `uv run pytest` 不会收集下面这两个。要显式写路径。

```bash
uv run pytest docker/browser_rpc/tests -q      # browser-rpc，跑在 fake 后端上，不需要浏览器
(cd docker/downloader && go test ./...)        # Go 写的媒体下载器
```

`docker/browser_rpc/tests/conftest.py` 会把 `docker/` 加进 `sys.path`，所以尽管这个服务是带着自己那套依赖单独打镜像的，在 checkout 里 `import browser_rpc` 也能工作。

### 覆盖率

CI 在 unit job 上带着 `--cov=src/dtk --cov-report=term-missing` 收集覆盖率。**没有覆盖率下限门禁**——不会有任何东西因为一个百分比而失败。`[tool.coverage.run]` 统计 `src/dtk`，排除 `*/migrations/*`。

## 质量门禁

下面就是 CI 跑的检查。它们都能在本地跑；只有镜像构建需要网络——拉基础镜像和下载依赖。

| 门禁 | 命令 | CI job |
| --- | --- | --- |
| 格式 | `uv run ruff format --check src tests` | `static` |
| Lint | `uv run ruff check src tests` | `static` |
| 类型 | `uv run mypy` | `static` |
| 单元 + 回放 | `uv run pytest tests/unit tests/replay -q` | `unit` |
| 集成 | `uv run pytest tests/integration -q -m integration` | `integration` |
| 控制台类型 | `(cd web && npm run typecheck)` | `console` |
| 控制台 lint + i18n | `(cd web && npm run lint)` | `console` |
| 控制台构建 | `(cd web && npm run build)` | `console` |
| compose 文件可解析 | `docker compose -f docker/compose.yml config -q`，再 `docker compose -f docker/compose.test.yml config -q` | `image` |
| 镜像可构建 | `docker build -f docker/Dockerfile -t dtk:ci .` | `image` |

Python 这侧有简写：

```bash
make fmt     # 先 ruff format，再 ruff check --fix
make lint    # 先 ruff check，再 ruff format --check
make type    # mypy
make test    # 单元 + 回放，然后集成，夹具由它替你管
```

`make fmt` 是唯一会写文件的那个；在 `make lint` 之前跑它，格式门禁就会过。

整栈的端到端检查会拉起 compose 项目、走一遍初始化流程、签发一个 API Key、验证文档里承诺的契约，并且总是把自己拆干净：

```bash
make smoke        # ./scripts/smoke.sh
./scripts/smoke.sh --keep   # 留着栈不拆，方便你上去看
```

它不在 CI 里。它要跑几分钟而且需要 Docker，值得在发版前、或者改过 compose 文件、entrypoint、初始化流程之后跑一次。

### 各工具的实际配置

**ruff**（`[tool.ruff]`）：行宽 100，target `py312`，启用规则集 `E`、`F`、`W`、`I`（import 排序，`dtk` 视为 first-party）、`N`、`UP`、`B`、`C4`、`SIM`、`RUF`。忽略：`E501`、`B008`、`N818`、`RUF001`、`RUF002`、`RUF003`。

其中两条值得理解而不是绕开。关掉 `E501` 是因为行宽由**格式化器**在 100 处负责——一条长 URL 或一条长注释不算 lint 错误。`N818` 要求每个异常类都带 `Error` 后缀；而这里的领域异常是按它们稳定的线上错误码命名的（`UpstreamChanged`、`SecretKeyMissing`），类名和调用方分支所依据的契约保持一致，后缀由基类承担。

**mypy**（`[tool.mypy]`）：Python 3.12，`packages = ["dtk"]`，`mypy_path = "src"`，`ignore_missing_imports = true`，`warn_unused_ignores = true`。`uv run mypy` 不带参数检查的就是对的东西。

它不是全局 strict，这是一个刻意的梯度：三个包——`dtk.platforms.*`、`dtk.models.*`、`dtk.scheduler.*`——额外开了 `disallow_untyped_defs`。这三处正是没有类型标注会真出事的地方：两个平台都必须满足的数据契约，以及决定用哪个身份的那部分算术。

**ESLint**（`web/eslint.config.js`）：JS 与 TypeScript 的 recommended 规则集、`react-hooks`，另外把 `@typescript-eslint/no-explicit-any` 和 `no-non-null-assertion` 设为 error，再加三条为这个项目写的规则——见下一节。

## 代码规范

有些是约定，有些是自动检查。凡是有检查的地方我都点了名，因为背后有检查的规则是你可以依赖的规则，而不是你必须记住的规则。

### 源码只用英文

`src/dtk/**/*.py` 和 `web/src/**/*.{ts,tsx}` 里不允许出现 CJK——注释、标识符、字符串一律如此。中文只放在 `src/dtk/i18n/locales/*.json` 和 `web/src/locales/zh/*.json`，别处没有。

这条被检查两次：Python 侧是 `tests/unit/test_repo_hygiene.py::TestSourceLanguage`，控制台侧是 `local/no-cjk-source` 这条 ESLint 规则。用母语写注释永远是阻力最小的选择，所以只有机器检查才守得住这条线。

测试里**只有作为输入数据时**才可以出现 CJK——真实用户粘贴的就是中文分享口令，URL 提取器必须处理得了，假装不是这样反而会削弱测试。为此有三个文件在白名单里：`tests/unit/test_urls.py`、`test_i18n.py`、`test_identity_importing.py`。即便在这些文件里，凡属于代码的部分——命名、注释、断言——依然是英文，而且 `tests/unit/test_i18n.py` 把中文期望值写成 `\uXXXX` 转义，并在旁边注明每个转义念作什么。

唯一的豁免是项目的标识——那只从 v1 起就蹲在各个入口文件顶部的 ASCII 猫，它有一部分是用片假名画的。豁免是**按形状而不是按文件名**给的，规则写在 `tests/support/marks.py`：只有当一行里的每个字符都来自这只猫自己的那套小字母表时才放行，所以夹带一个真词混在猫旁边的行不算豁免。

### 面向用户的文案一律来自词条表

在控制台这是一条 lint 规则，不是评审意见。`local/no-untranslated-text` 会报出 JSX 文本里的字面散文、一组可翻译属性里的（`label`、`placeholder`、`title`、`description`、`hint`、`summary`、`caption`、`confirmLabel` 等等）、`{cond ? 'Yes' : 'No'}` 这类表达式里的，以及对象字面量里同名属性上的——因为列定义和选项表通常声明在离渲染它们的 JSX 很远的地方。

标识符不是散文，按模式豁免：`SCREAMING_CASE` 的错误码、带点的配置键和接口名、版本号、IANA 时区、URL，以及 `<code>`、`<pre>`、`<kbd>`、`<samp>` 或带 `u-mono` / `mono` class 的元素内部的一切。如果你这一处确实是例外，加一行 `eslint-disable-next-line` 并写明理由。永远不要翻译错误码、枚举值、字段名、接口路径或配置键——要翻译的是它们的*显示标签*。

### 颜色只来自 token

`.ts` 和 `.tsx` 里不允许出现十六进制颜色字面量。`local/no-raw-hex-color` 会在这些文件里出现一个时让构建失败，字符串里和模板字面量里都算，`tests/unit/test_repo_hygiene.py` 再对同一批文件兜一次底。ESLint 在这里根本不解析 CSS，所以 `.module.css` 里的十六进制颜色靠的是约定而不是检查——目前树里唯一刻意的例外是赞助标识后面那块白底，它就地写了注释。用 `var(--accent)`、`var(--danger)`、`var(--bg-raised)`。

两套主题都是完整实现，而不是“一套底色加一层滤镜”，`tests/unit/test_repo_hygiene.py` 断言深色块和浅色块定义的 token 名完全一致。只在一边定义的 token 会精确地、悄无声息地弄坏一个主题里的一种颜色。

### 注释解释“为什么”

代码本身说清了它在做什么。注释的价值在于记下某个决定的理由——某个数字背后的测量、某个防护是被哪次故障催生的、试过又行不通的那个方案。这份代码里大量注释都是这个形状，也正是它挡住了下一个人去“修好”一个刻意的取舍。如果某个数字来自测量，就把测量本身写进去：测了什么、什么时候测的。

### 交付的代码里不留残留物

`tests/unit/test_repo_hygiene.py` 会因为 `src/dtk/` 里任何位置的 `TODO`、`FIXME`、`XXX` 而失败，也会因为 `NotImplementedError` 出现在两种正当用法之外而失败（抽象基类方法，那是一份契约；以及 `contextlib.suppress(NotImplementedError, ...)`，那是调用方应对某平台不具备某能力的写法）。

### 密钥永远不进仓库

`.env` 和 `.env.*` 都被 git 忽略，唯一的例外是 `.env.example`，而 `.env.example` 里那三个密钥变量必须留空——有测试断言这件事。另一条检查会扫描 Python 目录树里任何形似真实会话 Cookie 的东西。这是 V4 留下的教训：一份真实的抖音会话曾经躺在被跟踪的 `config.yaml` 里，离被公开只差一条命令。

### 分层规则：这是结构约束，不是风格偏好

- **`platforms/` 不做任何 IO。** 不发 HTTP、不读配置、不碰数据库。平台包负责构造请求描述和解析响应体；重试、签名、代理、Cookie、日志和缓存都在它之上。正是这一条让回放层成为可能。
- **解析器是纯函数。** `dict` 进，模型出。必填字段缺失时抛 `UpstreamChanged`，并带上缺失的点分路径；响应本身是一次拒绝时抛 `UpstreamRiskControl`。永远不允许返回一个填了一半的模型。
- **`transport/` 之上的任何东西都不 import wreq。**
- **`urls/` 之外不解析平台 URL。** 这个包同时是调用方所传 URL 的 SSRF 收口点。
- **`core/` 不 import 包内任何其他东西。** 其余所有人都可以 import 它。

## 新增一个平台接口

下面这个例子是给已有平台加一项能力。加一整个平台是同样的活儿再加一个新的 `src/dtk/platforms/<name>/` 目录——而且仅此而已：适配器在 import 时通过扫描“暴露了 `adapter.ADAPTER` 的子包”被发现，没有任何注册表要改。`tests/replay/test_platform_contract.py` 直接断言了这条性质。

1. **声明接口。** 在 `src/dtk/platforms/<platform>/endpoints.py` 里加上 URL 常量、一个稳定的名字常量（`douyin.author_followers`，即 `<platform>.<capability>`），以及 `ENDPOINTS` 表里的一条 `EndpointSpec`。这条 spec 带 `required`、`build` 可调用对象、`risk_weight` 和 `summary`。

   这个名字是**运维契约**的一部分：调度器把它原样用作 Redis 令牌桶和熔断器的 key，控制台在接口健康看板上展示它，`request_log` 的每一行都带着它。不要随手改名。

2. **构造查询串。** 把构造器加到 `src/dtk/platforms/<platform>/params.py`。值保持未编码——百分号编码只在传输层做一次，因为签名是对编码后的查询串计算的，而编码两次是 V4 时代反复出现的签名失败原因。浏览器形状的值（屏幕尺寸、浏览器版本、语言）通过 `ClientProfile` 传入而不是写死在这里，这样查询串才能和该身份的 User-Agent 与 TLS 仿真对得上。

3. **解析响应。** 在 `parser.py` 里新增或扩展解析器，并挂到适配器上。用 `src/dtk/platforms/common.py` 里的辅助函数，好让两个平台面对同一处歧义时做出同一个判断：缺失就是 `None`，绝不是 `0` 或 `""`；标识符一律 `str`；必填字段缺失时抛出带路径的 `UpstreamChanged`。

4. **给它一条调度策略。** 往 `src/dtk/scheduler/policies.py` 加一条 `EndpointPolicy`。这在实践中不是可选项：`tests/unit/test_policy_coverage.py` 会在某个已注册的接口没有显式策略时失败，因为名字对不上并不会抛异常——它会悄悄落到 `DEFAULT_POLICY`，所有调好的配额随之失效而没有任何东西提醒你。同一个测试还会拒绝指向已不存在接口的策略，并断言一条合理策略的形状：`max_concurrency == 1`、补充速率不超过 1/s、突发在 1 到 10 之间、`risk_weight >= 1.0`。

   列表类接口的限流比详情查询更狠，这一点也有测试断言。翻遍一个作者的全部作品正是平台最盯的模式；而一次详情查询是真实用户会做的最廉价的调用。

5. **接上面向调用方的参数名。** 在 `src/dtk/worker/registry.py` 里，如果是新能力就加一个 `Capability` 值，然后在 `_ARGUMENTS` 里加一条，把规范参数名（`content_id`、`author_id`、`unique_id`、`mix_id`、`comment_id`、`cursor`、`count`）映射到该平台自己的参数名，再在 `_CACHE_TTL_KEYS` 里加一条。注册表会在 import 时拿你的映射去比对真实的构造器签名，所以拼错是一个 import 错误，而不是一个运行时错误。

   `P0_CAPABILITIES` 里的能力是每个平台都必须具备的。此外的都是可选、按平台声明，因为两个平台确实不一样——TikTok 把作品归到它自己暴露成独立列表的合集里，抖音只对已登录的身份提供作者的喜欢列表。接口健康看板 `GET /api/v1/admin/endpoints/health` 按平台列出每一个已声明的接口，控制台上的运维者据此能看清哪项能力在哪个平台上有；普通调用方则是从 `UNSUPPORTED_CONTENT` 这个错误里知道的——它的 `details.supported` 写着哪些平台提供这项操作。

6. **暴露出去。** 在 `src/dtk/api/routes/content.py` 里加一条 REST 路由，用 `openapi_extra={I18N_KEY: "<key>", **ASYNC_RESPONSES}` 声明，并在 `src/dtk/i18n/locales/en.json` 和 `zh.json` 两边都写上 `openapi.op.<key>.summary` / `.description`。`tests/unit/test_i18n.py` 和 `tests/unit/test_openapi_completeness.py` 会因为“操作没有文案”“参数没有 `description=`”“中文文档里还是英文”而失败。路由函数的 docstring 就是 FastAPI 用作英文描述的那段。

   MCP 是另一个决定。工具集刻意封顶在八个——每多一个工具，代理选对工具的准确率都会有可测量的下降——所以一项新能力应该成为现有工具的一个参数，而不是第九个工具。见 [MCP 与 AI 客户端](./12-mcp.md)。

7. **补 fixture 和回放测试。** `tests/fixtures/<platform>/` 下每个平台一个 JSON 文件，按该目录 README 的规则脱敏，再在 `tests/replay/` 里加一个测试。生产上某个接口坏掉时，第一步就是把那次响应存到这里并写一个失败的回放测试，然后再修解析器——这样每一次故障都会留下一道永久的回归防线，而不是被忘掉。

## 新增一个控制台页面

1. `web/src/pages/<Name>.tsx`，默认导出组件。从一个 `PageHeader` 起手往外搭；用 `@/components` 里的现成组件，不要自己手搓表格或弹窗。
2. 在 `web/src/App.tsx` 里加一个 `lazy()` 导入和一条 `<Route>`。每个页面都是代码分割的。
3. 在 `web/src/lib/nav.ts` 的 `NAV_ITEMS` 里加一条：`path`、`labelKey`、取自 `NAV_GROUPS` 的 `group`（`monitor`、`pool`、`tools`、`access`、`operations`），以及取自 `NavIconName` 的 `icon`。这个图标名必须在 `web/src/components/Sidebar.tsx` 的 `NAV_ICONS` 映射里存在。一个真实存在但不属于任何分组的页面放进 `UNLISTED`，这样面包屑还能叫得出它的名字。
4. 在 `web/src/locales/en/console.json` 和 `web/src/locales/zh/console.json` **两边**都加词条：侧边栏标签用 `nav.<key>`，页头用 `page.<key>.title` 和 `page.<key>.description`。页面自己的词条也在同一次提交里加上——`npm run lint` 会因为一个 `t()` 调用引用了任何词条表都没有的 key 而失败，也会因为你在 JSX 里留下的字面文案而失败。

设计系统期待的一些约定，评审也会照着看：

- **状态 = 颜色 + 图标 + 文字。** 用 `<StatusBadge kind="…" value={…} />`，而不是给单元格上色。`business_error` 渲染成弱化色，绝不是红色：一个被删掉的视频不是系统故障。
- **ID 和 JSON 用等宽。** ID 用 `<CopyableId>`，载荷用 `<CodeBlock json={…}>`。
- **轮询出来的值不做动画。** 给 `DataTable` 传 `flashValue`，让某一行在它的*状态*变化时闪一次；不要加那种每次刷新都会触发的过渡。
- **焦点框要留着。** 没有可见替代方案就不许写 `outline: none`。所有东西都要能用键盘到达和操作，浮层要能用 Esc 关掉。
- **给 `DataTable` 一个 `storageKey`**，这样访问者选的列和密度能留住。
- **轮询用 `POLL.fast` / `POLL.normal` / `POLL.slow`。** 不要自己发明间隔。
- **手机上必须还能用。** 768px 以下侧边栏变抽屉、表格变卡片。凌晨三点的告警就落在那块屏幕上。

## 新增一个配置项

运行时配置在首次初始化时从环境变量播种一次，之后就住在 `settings` 表里，可以在控制台改而无需重启。目前一共 54 项，分 14 组。完整参考见[配置参考](./03-configuration.md)。

1. **声明它**：在 `src/dtk/core/config.py` 的 `RUNTIME_SETTINGS` 里加一条 `SettingSpec`：键名（`<group>.<name>`）、默认值、`Scope`、类型，以及一句给开发者看的说明。不在这张注册表里的键根本存不进去，正是这一点让一个拼写错误不会悄悄变成一个配置键。

   老老实实选作用域。`RUNTIME` 是常规情况。`SENSITIVE` 意味着把这个值放宽会扩大攻击面——CORS 来源、URL 白名单、任务 webhook 开关都属于此类。`BOOTSTRAP` 和 `ENV_ONLY` 只走环境变量，不在这条流程里。

   受限字符串用 `choices`，类型表达不了的约束用 `validate`。校验器返回的是“要存下去的值”，所以它既可以拒绝也可以规范化——而且它应该指名道姓地拒绝，而不是把一个荒谬的值悄悄捏成一个看着合理的。`_percent` 拒绝 1–99 之外的一切，因为 0 会让实例一启动就暂停、150 则意味着这道保护永远不触发；`_byte_ceiling` 允许 0 表示“不限”，但拒绝任何小于 1 MiB 的正数。

2. **两种语言都要解释它。** 往 `src/dtk/i18n/locales/en.json` 和 `zh.json` 加 `settings.description.<key>`。这不是可选的：`tests/unit/test_i18n.py` 会把所有没有词条说明的配置项列出来并失败，另一个测试会在说明只是把 key 人性化地回显一遍时失败。注册表里那句说明是写给开发者的备注，不是用户读到的文本。

3. **如果分组是新的**，把它加进 `web/src/pages/Settings.tsx` 的 `GROUP_ORDER`——如果它属于调度器，就加进 `web/src/pages/Scheduler.tsx` 的 `SECTIONS`，`sched` 和 `pool` 就渲染在它们作用的那个池子旁边。然后在两份控制台词条表里加上 `settings.group.<group>` 和 `settings.groupHint.<group>`。少了这一步，这个配置项会落到“其他”里、没有自己的标题，而 `tests/unit/test_repo_hygiene.py` 会把这件事说出来。

4. **如果它有 `choices`**，就在两份控制台词条表里为每一个选项加上 `settings.choice.<key>.<choice>`。一个选项全是裸键名的下拉框逼着读者去猜它们的含义，有测试在管这件事。

## 新增一条翻译文案

两套词条系统，彼此不重叠。

**后端**，在 `src/dtk/i18n/locales/`：

| 文件 | 放什么 |
| --- | --- |
| `en.json`、`zh.json` | API 渲染出的一切散文：配置项说明、OpenAPI 的操作摘要与描述、参数与请求体字段文案、诊断建议、状态与结果标签。 |
| `errors.en.json`、`errors.zh.json` | 每个 `ErrorCode` 一条消息模板，按错误码名做 key。 |
| `format.en.json`、`format.zh.json` | 本地化格式：数量刻度（`K`/`M`/`B` 对 万/亿）、相对时间措辞、时长格式。 |

**控制台**，在 `web/src/locales/{en,zh}/`：四个命名空间——`common`、`console`、`errors`、`setup`。

两侧都成立的规则：

- **`en` 和 `zh` 的 key 集合必须完全一致。** 少一个 key 会静默回退到英文而没人察觉，所以它是失败，不是警告。
- **占位符在各语言之间必须完全相同**，控制台的消息在两种语言下都必须能按 ICU 解析。
- **译文不能是英文原文的复制。** 有测试专门找这个。
- **永远不要翻译**错误码、枚举值、字段名、接口路径或配置键。要翻的是显示它们的那个标签。

`(cd web && npm run lint)` 会跑 `web/scripts/check-i18n.mjs`，它检查以上全部，另加两件值得知道的事：

- **语言切换器里的语言名是本族名（endonym）**，且在所有词条表里完全相同。一个读不懂当前界面语言的人，正是在这个下拉里找自己那门语言的名字；把这个名字也翻译掉，恰恰就是把它藏起来。
- **每一个 `t()` 里写死的 key 都必须真实存在。** 一致性只说明两份文件彼此吻合，它完全说明不了控制台要的 key 是不是这两份文件里有的。一个拼写错误会静默上线、在抽屉中间渲染成原始 key——这条检查正是因为出过这种事才存在。由变量拼出来的 key 无法在这里解析，会被跳过；调用处传了 `defaultValue` 就等于声明这个 key 可以缺席，也被允许。

Python 侧，`tests/unit/test_i18n.py` 强制同样的一致性，检查每个 `ErrorCode` 在每种语言下都有翻译，并断言词条文件保持是合法 JSON。运行时的策略和 CI 的策略刻意相反：出现缺口时记一条警告并回退到英文，而不是拒绝启动——因为这些文件在自托管安装里是可以直接编辑的 JSON，一个译者的手误应该只代价一句话，而不是整个实例。

### 新增一个错误码

错误码是线上契约，所以它牵动四个地方，除了第一处之外每一处都有检查兜着：

1. `src/dtk/core/errors.py` 里的 `ErrorCode`，加上它在 `HTTP_STATUS` 里的条目，以及——如果重试永远无济于事——`NON_RETRYABLE`。`HTTP_STATUS` 里有没有这一条没有任何测试在管：漏了它，第一次有东西问这个码的状态码时会在运行时抛 `KeyError`。所以加枚举成员的那一次编辑里就把它补上。
2. `src/dtk/i18n/locales/errors.en.json` 和 `errors.zh.json`。
3. `web/src/locales/en/errors.json` 和 `zh/errors.json` 里的 `code.<CODE>` 与 `hint.<CODE>`。
4. `web/src/lib/api.ts` 里的 `ERROR_CODES`。`isErrorCode()` 以这个数组为准，所以少了一个码就会被静默改写成 `INTERNAL`——服务端明明精确解释了的情况，控制台却显示成“发生了意外的内部错误”。

`check-i18n.mjs` 会直接从 `errors.py` 里解析出这个枚举，并对第 3、4 项的缺口报错——第 4 项还是双向的：既包括没有控制台文案的错误码，也包括 API 从不返回却出现在控制台里的错误码。它只读 `web/src/locales/`，所以第 2 项（后端词条表）不归它管，那一项由 `tests/unit/test_i18n.py` 兜着。

## 提交信息

Conventional Commits 的前缀，scope 可选：`feat`、`fix`、`docs`、`test`、`chore`、`refactor`、`perf`、`ci`。在用的 scope 有 `console`、`signing`、`scheduler`、`services`、`db`、`api`、`i18n`、`core`。

标题行在前缀之后用小写，讲的是**行为上改了什么**，不是哪些文件挪了位置：

```
fix: a post that does not exist is an answer, not risk control
feat: show what the refill job is doing, failures included
feat(console): add an MCP page to the console
```

正文是这个项目的提交历史真正有价值的地方，长正文在这里是常态。写清楚你测了什么、试过什么、刻意没有动什么，如果这是个修复，还要写清原来的失败形态是什么。如果 diff 里某个数字来自测量，就把测量本身写进提交信息：两个身份、两次响应、各自多少字节、哪一天测的。半年后读这份 diff 的人推导不出这些，代码里的注释也承载不下全部。

## 提交 Pull Request

PR 提到 **`main`**。CI 对 `main` 的 `push` 和 `pull_request` 生效，另外也检查提到 `v4` 的 PR——那个分支冻结了，但仍然收安全修复，而收 PR 却不检查是说不过去的。

推 `main` **不会**构建 Docker 镜像。发布是单独一个动作：把 `main` 快进到 `release`，或者打一个 `v*` 标签。这样 `latest` 指向的是有人决定要发布的那个提交，而不是一小时前刚合进去的东西。

开 PR 时会自动带出一份中英双语模板，下面这份清单就在里面。只想要一种语言的话，在 compare 链接后面加 `?template=zh.md` 或 `?template=en.md`。开 PR 之前：

1. `make fmt`——这样格式门禁是通过，而不是败在空白字符上。
2. `make lint && make type`——ruff 和 mypy。
3. `make test`——单元、回放和集成，夹具由它替你管。
4. `(cd web && npm run verify)`——只要你动过 `web/` 下的东西。
5. 如果你改了读者会照着做的东西——一个配置项、一条 CLI 命令、一个接口、一个控制台页面、一个默认值——那就同时更新 `documents/en/` **和** `documents/zh/` 里的用户文档。两种语言是同一份文档，理应保持同步。
6. 检查你新加的东西里没有：源码中的 CJK、tokens 文件之外的十六进制颜色、JSX 里的字面文案、`TODO`、`.env`、含真实 Cookie 的 fixture。这六样卫生测试都会抓，但自己先发现要快得多。

在描述里写清这次改动做了什么、为什么，以及你是怎么验证的。有些东西你验证不了——需要真实平台的代码路径、你本机没装的浏览器后端——那也照实说。一句诚实的“没有对着真实浏览器验证过”，比一个需要评审去证伪的笃定说法有价值得多。

CI 有五个门禁 job：`static`、`unit`、`integration`、`console`、`image`。第六个 `contract` 永远不阻塞合并——它挂在一个这个 workflow 还没声明的 `schedule` 触发器上，所以它在 CI 里根本不会运行，需要时由你手工跑。

## 相关页面

- [安装与部署](./02-installation.md)——你要拿来验证的那套 compose 技术栈。
- [配置参考](./03-configuration.md)——每一个配置项、它的作用域和默认值。
- [核心概念](./04-concepts.md)——身份、调度器、请求结果、熔断器。
- [REST API 指南](./11-api.md)——响应信封、权限范围、`?wait=`、`?refresh=`、`?explain=`。
- [MCP 与 AI 客户端](./12-mcp.md)——那八个工具，以及为什么只有八个。
- [命令行参考](./13-cli.md)——每一条命令，以及哪些对着线上实例是安全的。
- [故障排查](./14-troubleshooting.md)——在断定是 bug 之前先把失败读懂。
- [安全](./15-security.md)——主密钥、各种白名单，以及什么会离开这个实例。
