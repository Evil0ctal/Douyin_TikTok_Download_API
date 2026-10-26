# One-command install / 一键部署

**English below · 中文见下半部分**

Two scripts, same behaviour, different language. Both install with **Docker
only** — if you want to run without Docker, that is a document rather than a
script: [documents/en/02-installation.md](../documents/en/02-installation.md).

| | Script | Docs |
|---|---|---|
| English | [`install.sh`](./install.sh) | [Installation](../documents/en/02-installation.md) |
| 中文 | [`install.zh.sh`](./install.zh.sh) | [安装与部署](../documents/zh/02-installation.md) |

---

## Read it before you run it

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh -o install.sh
less install.sh          # about 2900 lines, every one of them commented
bash install.sh
```

That is the recommended order, and it is not ceremony. Anything you pipe into a
shell runs as you, and on the Docker step it offers to run something as root.
The script is written to be read: it says what it is about to do before each
step and asks before every change.

The single line, if you would rather:

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh | bash
```

Piping still works, including the questions — the script reads answers from your
terminal rather than from stdin, because with `curl | bash` stdin is the script
itself. If there is no terminal at all it says so instead of quietly taking
defaults.

## Run it again to manage the instance

The script works out which job it is doing. If there is already an install, it
opens a menu instead of installing. It looks in `DTK_INSTALL_DIR`, in the
install the script itself belongs to or is run from (so
`bash install/install.sh --manage` inside one manages that one, whatever its
project is called), in Docker's labels on the project's containers (so it does
not matter where you put it), and in the default directory:

```
==> An install is already here
    ✓ /opt/dtk
    ✓ Running 5.0.0
    ! v5.0.1 is available.

      1  Status - versions, containers, disk
      2  Upgrade to v5.0.1
      3  Manage - passwords, backups, settings, disk
      4  Stop or remove this install
      q  Quit
```

It looks every time — with `--yes` and with `DTK_INSTALL_DIR` set too, and in
the default directory as well as through Docker. With `--yes`, or with no
terminal, finding one changes nothing: it says where the install is, points at
`./dtkctl upgrade`, and exits 0.

**Upgrading** compares the running version against the latest v5 release on
GitHub — a pre-release suffix counts as older, so `5.1.0.dev0` is behind
`5.1.0`, and the v4 branch's `V4.x` tags are never taken for the latest. It
moves the checkout to that release's tag and pins `DTK_IMAGE_TAG` to the version
rather than following `latest`, so you can say what is running and put it back,
and it writes that pin itself: nobody edits `.env` to upgrade.

The order is the point of it. The checkout moves first; then come the steps
that can fail without touching what is running — Compose's check of the new
configuration, the pull (or the build), the browser image, a backup with
`dtk backup create` into the backup volume, and the migrations — and only then
is the version written into `.env` and the stack restarted. Anything that fails
before the restart, Ctrl-C included, puts the checkout and `.env` back as they
were (it says `Put back: …`), and the version that was running is still
running. The backup is taken only when the API is up; a failed one stops the
upgrade, and `--no-backup` skips it. The data stays in its volumes.

Going back to an older release works only as far as one that knows every
migration the database has had. Migrations do not run backwards, so a release
without the latest one is refused before anything is pulled; the way back then
is a new install of that version with a backup taken on it restored into it,
under the same `DTK_SECRET_KEY`, without which the archive does not open.

The same upgrade without the menu, from the install directory:

```bash
./dtkctl upgrade                    # the latest release
./dtkctl upgrade 5.1.6              # or a given one: 5.1.6, v5.1.6, 5.1 (the newest 5.1.x), sha-<commit>
./dtkctl upgrade 5.1.6 --no-backup  # without the backup first; the version goes before the flag
```

It moves the checkout first and then hands over to the copy of this script
that came with that release, so the steps are always the new version's. Run it
again when one stopped half-way: "Already on" needs the running version, `.env`
and the checkout to agree, so it finishes the job. An install that builds from
source has no version to choose: its checkout follows main, and it rebuilds its
images from that instead of pulling. `dtkctl` itself is rewritten only when it
has no `upgrade` command yet, so anything you added to its compose line
survives. An install from before 5.1.6 has no `upgrade` until its first
upgrade — from the menu, or once with
`git pull && bash install/install.sh --upgrade` in its directory. A checkout
moved back to a release older than 5.1.6 has none of its own either, and
`./dtkctl upgrade` says so, with the command to run instead: the current
installer with `--upgrade`.

An upgrade also takes out a `CLOAKBROWSER_COMMIT` line that this project wrote
into `.env` itself — `f04c23da285b…` (CloakBrowser 0.5.10, from an old
`.env.example`) or `fbf005bc1999…` (0.6.3, from the installers of 5.1.4 to
5.1.6) — and leaves a comment in its place, so the browser image follows the
checkout from then on. A revision you chose is kept, with a warning that the
checkout's browser dependencies were verified with another one. When the
revision changes, the browser rebuild downloads a new Chromium and takes several
minutes.

**Manage** covers the things people otherwise ask how to do:

| | |
|---|---|
| Change a password | `dtk user passwd`, read without echo and passed on stdin; an empty answer cancels |
| Add an administrator | There is no rename — accounts are created and reset, not renamed |
| List accounts | |
| Back up / restore | Into the backup volume, which survives a rebuild. Restore takes a name from the list, and adds what the archive has and the database lacks: nothing already there is overwritten, so it does not roll an instance back |
| Self check | `dtk diagnose`, the six-step one |
| Logs | The last lines of each service |
| Restart | Applies `.env` and compose changes (`up -d`), then restarts the application services — not postgres or redis |
| Settings | Any runtime setting, including `retention.*` — no restart needed. The full list on an empty name; the new value is taken as typed, spaces included, confirmed, and passed on stdin |
| Free disk space | Images of **this install only** that nothing uses, and optionally Docker's build cache |
| Transcription on or off | For an install that already exists. On: the same model step as the install, then the image, `.env` and a recreate; if it does not come up healthy, it is switched off again. Off: the container goes, the models stay on disk. Only the `--profile transcriber` flag in `dtkctl` is touched |

Free disk space is worth a note. Container logs are already capped by the compose
file at 10 MB × 3 per service, so the whole stack cannot pass about 180 MB of
logs. What grows without a ceiling is old image tags and buildx cache, and the
cleanup is scoped to this install — a plain `docker image prune -a` would take
unrelated images off a host running other stacks. It offers only images that no
container uses and this install's configuration does not name, so a stack
stopped with `down` keeps its browser image; an image built on this machine is
offered only when Compose labelled it as built for this project. The build cache
is Docker's, shared by everything the machine builds, and the question says so.

**Stop or remove** has three levels: stop and keep everything, stop and delete
the volumes, or that plus the install directory. Stop is `stop`, not `down`:
the containers stay, so the next run still finds the install, and
`./dtkctl up -d` starts it again. The last two require typing `delete`, not a
y/n, and print how to copy the backups out first
(`./dtkctl cp api:/var/lib/dtk/backups ./backups`); if removing the stack fails
they stop there, before anything else is deleted, and the directory removal
refuses anything that does not look like an install. Back returns to the menu.

`--manage` goes straight there.

## What it asks

Seven questions, all with a default you can accept by pressing enter. Ctrl-C at
any of them stops the script; it does not take the default and carry on.

1. **Where to install.** `/opt/dtk` as root, `~/dtk` otherwise.
2. **Which address and port.** `127.0.0.1:8000`. Choosing `0.0.0.0` warns you,
   because that puts an unencrypted admin console on the network.
3. **The browser container.** It mints guest identities by itself so you never
   paste cookies, and it costs about 4 GiB of RAM and a 6 GB image built on this
   machine. Suggested off under 6 GiB of RAM, and always off with `--yes`.
   It installs CloakBrowser at the commit `docker/compose.yml` names, the one
   the checkout's browser dependencies were verified with, and does not write it
   into `.env`, so every upgrade moves it along with the checkout. An older
   install whose image has no browser in it gets one from Upgrade (or
   Reinstall) in the menu.
4. **Media downloads.** Small and cheap. On by default.
5. **Offline transcription.** Turns stored videos into text on this machine:
   about 1 GiB of RAM, two cores while it works, a 1.1 GB image and 500 MB of
   models. Off by default, and off with `--yes` unless `DTK_ENABLE_TRANSCRIBER=1`.
   It is not offered without media downloads (it transcribes what they stored)
   or on a single core, and under 4 GiB of RAM a yes comes with a reminder to
   add swap. Say yes and it asks where the models are — see below.
6. **Published images or build from source.** Published, unless you say
   otherwise.
7. **Move the checkout to the release being installed**, if that directory
   already holds a checkout of this project.

## What it does

- Detects the distribution and offers the right way to install Docker. Debian,
  Ubuntu and derivatives; RHEL, CentOS, Rocky, AlmaLinux, Fedora, Oracle,
  Amazon Linux; openSUSE and SLES; Arch and Manjaro; Alpine; NixOS, Void,
  Gentoo. macOS points at Docker Desktop, WSL points at the Windows side.
- Refuses to guess on 32-bit or anything that is not x86_64 or arm64.
- **Installs a release, not main.** It clones `main` — its whole history, about
  14 MB — and checks out the latest release's tag, detached, because main runs
  ahead of the published images. `.env` gets that version as `DTK_IMAGE_TAG`
  (`5.1.6`, say), not `latest`.
- **Scales the resource limits to what Docker can use** — on Docker Desktop or
  OrbStack that is the VM, not the machine around it. `docker/compose.yml` is
  written for 4 vCPU and 8 GiB. Docker refuses outright to start a container
  asking for more CPUs than exist, so on a smaller host the script writes a
  `compose.host.yml` with ceilings that fit: no CPU ceiling above the host's,
  `migrate` and the transcriber included, a `memswap_limit` that follows each
  `mem_limit` it lowers, and on a small host `DTK_REDIS_MAXMEMORY` in `.env`,
  under redis' new ceiling. A `compose.host.yml` it would rewrite that differs
  from what it writes is kept as `compose.host.yml.old`.
- Generates every secret with `openssl rand`, writes `.env` under `umask 077`,
  and never prints one.
- Writes a `dtkctl` wrapper that carries the three arguments which have to be
  right every time — the project name, both compose files, and
  `COMPOSE_ENV_FILES`, without which Compose reads `docker/.env` and silently
  interpolates defaults.
- Runs Compose's own `config -q` check as soon as those files are written, so a
  project name or port Compose rejects is reported as that, not as a failed
  pull.
- **Fetches the transcription models if you want transcription and do not have
  them.** The model directory defaults to `models/funasr-onnx` inside the
  install directory. One that already holds the ten runtime files is used as it
  is; one that holds other things and no `asr`, `vad` or `punc` is refused;
  otherwise it offers to download them from ModelScope (about 500 MB),
  pinned to the commits in
  [Preparing the FunASR ONNX models](../documents/en/18-funasr-models.md), and
  checks every file against its SHA-256. Permissions are changed on that
  directory and its `asr`, `vad` and `punc` only, never recursively on whatever
  was typed. If the models cannot be fetched, or the
  published transcriber image cannot be pulled — a release from before
  transcription has none — transcription is switched off and the rest of the
  install carries on; the menu turns it on later.
- Pulls, migrates, starts, waits for every service to report healthy, and prints
  the one-time link that creates the first administrator.

## What it will not do

It writes only inside the directory you choose, plus Docker's own volumes. It
never edits a file outside that directory, never adds a cron job, never opens a
firewall port, never installs anything without asking.

It uses `sudo` for exactly three things, each printed before it runs: installing
Docker if you say yes, starting the Docker service, and creating the install
directory when that needs root. It will not add you to the `docker` group — on
most machines that is the same as handing out root, so it tells you the command
and lets you decide.

It refuses to install into `/`, `/etc`, `/usr`, `/var`, `/home` and friends, into
a directory that is a checkout of some other project, or into a directory that
already has something in it.

It will not start a new install on top of an old one's data. When Docker already
has volumes for the project name and the directory has no `.env`, it stops and
says why — that database opens only with the old install's `.env`, and a new one
would generate new passwords — and offers three ways out: manage the old install
where it is, install beside it under another `DTK_PROJECT`, or delete those
volumes, for which it prints the exact `docker volume rm`.

## Options

```
--yes, -y   Take every default. Publishes on 127.0.0.1:8000, downloader on,
            browser container and transcription off.
--check     Detect the system and print what would happen, then stop. Changes
            nothing — good for seeing what it thinks of your machine first.
--manage    Go straight to the menu for an install that already exists.
--upgrade [VERSION]
            Upgrade the install that exists, asking nothing: to the latest
            release, or to VERSION (5.1.6, 5.1, latest or sha-<commit>).
            ./dtkctl upgrade runs this.
--no-backup With --upgrade: skip the backup taken before the migrations.
--help, -h  Everything above, shorter.
```

Every answer can be preset from the environment, which is what makes `--yes` a
real unattended install:

```bash
DTK_INSTALL_DIR=/srv/dtk DTK_BIND_PORT=9000 DTK_ENABLE_BROWSER=1 \
  bash install.sh --yes
```

A preset is checked like a typed answer, `--yes` or not: a bad port or path stops
the script with a message naming it. And the stack it starts runs on what went
into `.env`: `DTK_BIND_HOST`, `DTK_BIND_PORT`, `DTK_TRANSCRIBER_MODEL_PATH`,
`CLOAKBROWSER_COMMIT` and `DTK_IMAGE_TAG` left in your shell do not override it.

`DTK_VERSION` picks the release to install: `5.1.6`, `5.1` (the newest 5.1.x),
`latest` (the default) or `sha-<40-character commit>`. Its tag — a `sha-`
build's commit — is what is checked out. `DTK_REPO_URL` clones from a mirror
instead of GitHub; the release tags are then fetched from that mirror too.
`DTK_GH_PROXY` does the same with an `https://` prefix put in front of the
GitHub address. Either one is asked about before the clone: a mirror hands over
the whole install and every upgrade after it. See
[Cloning from a mirror, and what it risks](../documents/en/02-installation.md#cloning-from-a-mirror-and-what-it-risks).

Transcription takes two: `DTK_ENABLE_TRANSCRIBER=1` turns it on, and
`DTK_TRANSCRIBER_MODEL_PATH` says where the models are or should go. With both
and `--yes`, a directory without the models is filled by the checked download.

`CLOAKBROWSER_COMMIT` picks a different CloakBrowser revision for the browser
image; left unset, the image follows `docker/compose.yml`. It has to be a full
40-character commit, and it is written into `.env`, where upgrades leave it, so
the browser stops following the checkout; choose it deliberately.

`DTK_PROJECT` names the Compose project (default `dtk`). Set it to run a second,
completely separate stack on the same host. Compose's rule applies: lowercase
letters, digits, `-` and `_`, starting with a letter or digit.

## Requirements

`bash`, `git`, `curl`, and either `openssl` or `/dev/urandom` — no `python3`.
Alpine ships none of bash or git by default: `apk add bash git curl`.

Docker Compose **v2.24 or newer** — the compose file uses the long `env_file:`
form and `COMPOSE_ENV_FILES`, and older versions fail in ways that look like a
problem with this project.

## Installing from mainland China

Switch your mirrors first. The install pulls from Docker Hub, and building the
browser image also reaches GitHub, PyPI and a browser CDN. The transcription
models come from ModelScope, which is usually fast from mainland China. Without
a registry mirror the image pull usually times out and it looks like the
project is broken. If GitHub itself is slow, `DTK_REPO_URL` clones from a mirror
of this repository, or `DTK_GH_PROXY` through a mirror prefix. GitHub stays the
default, and a mirror is asked about first.
[中国大陆的网络准备](../documents/zh/02-installation.md#中国大陆的网络准备) has
the details.

---

# 一键部署

两个脚本，行为相同，语言不同。两者都**只支持 Docker** —— 不用 Docker 的部署方式是
一篇文档能讲清的事，不是一个脚本：[安装与部署](../documents/zh/02-installation.md)。

## 先读，再运行

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh -o install.zh.sh
less install.zh.sh       # 近三千行，每一行都有注释
bash install.zh.sh
```

推荐这个顺序，而且这不是走过场。任何你管道进 shell 的东西都以你的身份运行，而在
装 Docker 那一步，它还会问你要不要以 root 跑一个脚本。这个脚本是写来给人读的：
每一步之前都会说清自己要做什么，每一次改动之前都会先问。

想要真正的一行命令也可以：

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh | bash
```

管道运行时提问依然正常 —— 脚本是从你的终端读答案的，不是从标准输入读，因为
`curl | bash` 的时候标准输入就是脚本本身。如果压根没有终端，它会明说，而不是
默默取默认值。

## 再跑一次就是管理工具

脚本自己判断该做哪件事。如果已经有一份安装，它打开菜单而不是重新安装。它会依次在
`DTK_INSTALL_DIR`、脚本自己所属或运行时所在的那份安装（所以在一份安装里跑
`bash install/install.zh.sh --manage` 管理的就是那一份，不管它的项目叫什么）、Docker 给这个
项目的容器打的标签（所以装在哪都能找到），以及默认目录里找：

```
==> 这里已经有一份安装了
    ✓ /opt/dtk
    ✓ 运行中：5.0.0
    ! 有新版本 v5.0.1。

      1  状态 —— 版本、容器、磁盘
      2  升级到 v5.0.1
      3  管理 —— 口令、备份、设置、磁盘
      4  停止或移除这份安装
      q  退出
```

它每次都会找 —— 加了 `--yes`、设了 `DTK_INSTALL_DIR` 也一样，除了问 Docker，
还会看默认目录。加了 `--yes` 或者没有终端时，找到了也什么都不改：告诉你装在哪、
提示用 `./dtkctl upgrade`，然后以 0 退出。

**升级**会把运行中的版本和 GitHub 上最新的 v5 Release 比一下 —— 带预发布后缀的算旧，
所以 `5.1.0.dev0` 排在 `5.1.0` 前面；v4 分支的 `V4.x` tag 永远不会被当成最新版。
它把代码切到那个 Release 的 tag 上，并把 `DTK_IMAGE_TAG` 钉在具体版本上而不是
跟着 `latest` 跑，这样你说得出现在跑的是哪个，也换得回去；这个 pin 也是它自己写的，
升级不需要任何人去改 `.env`。

顺序是关键。先切代码；接着是那些失败了也碰不到正在跑的实例的步骤 —— Compose 对新配置
的检查、拉镜像（或构建）、浏览器镜像、用 `dtk backup create` 往备份卷里做一份备份、
跑迁移 —— 这些都过了，才把版本号写进 `.env` 并重启。重启之前任何一步失败，包括按了
Ctrl-C，代码和 `.env` 都会放回原样（它会明说放回了什么），原来在跑的版本照样在跑。
备份只在 API 跑着时才做；备份失败会让升级停下，`--no-backup` 可以跳过它。数据一直在
它的卷里。

退回旧版本，最多只能退到一个认得这个数据库做过的所有迁移的版本。迁移不会倒着跑，
所以缺了最新那次迁移的版本，在拉任何东西之前就会被拒绝；这时回去的路，是新装一份那个
版本，再把在它上面做的备份恢复进去 —— 用同一个 `DTK_SECRET_KEY`，否则归档打不开。

不进菜单也能升级，在安装目录里：

```bash
./dtkctl upgrade                    # 升到最新的 Release
./dtkctl upgrade 5.1.6              # 或者指定一个：5.1.6、v5.1.6、5.1（5.1.x 里最新的）、sha-<commit>
./dtkctl upgrade 5.1.6 --no-backup  # 先不备份；版本号写在这个参数前面
```

它先切代码，再把剩下的交给随那个 Release 来的那份脚本，所以跑的永远是新版本的步骤。
升级半路停下了就再跑一次：「已经是这个版本」要求运行中的版本、`.env` 和代码三者一致，
所以它会把没做完的做完。从源码构建的安装没有版本可选：它的代码跟着 main 走，用它重新
构建镜像，而不是拉取。`dtkctl` 本身只在还没有 `upgrade` 命令时才会被重写，所以你在它的
compose 那一行里加的东西不会丢。5.1.6 之前装的实例，要等第一次升级之后才有 `upgrade`
—— 从菜单里升，或者在它的目录里跑一次 `git pull && bash install/install.zh.sh --upgrade`。
代码退回到比 5.1.6 更早的 Release 时，它自己也没有这条命令，`./dtkctl upgrade` 会明说，
并给出该跑的命令：用当前的安装脚本加 `--upgrade`。

升级还会拿掉本项目自己写进 `.env` 的那行 `CLOAKBROWSER_COMMIT` —— `f04c23da285b…`
（CloakBrowser 0.5.10，来自旧的 `.env.example`）或 `fbf005bc1999…`（0.6.3，来自 5.1.4 到 5.1.6
的安装脚本）—— 原地留一行注释，从此浏览器镜像跟着代码走。你自己选的版本会保留，但会警告你
这份代码的浏览器依赖是用另一个版本验证的。版本一变，重建浏览器镜像要下载新的 Chromium，
得花上几分钟。

**管理**覆盖的就是大家平时会来问怎么做的那些事：

| | |
|---|---|
| 修改口令 | 走 `dtk user passwd`，不回显读入，从标准输入喂进去；直接回车就是取消 |
| 添加管理员 | 没有改名功能 —— 账号只能新建和重置，不能改名 |
| 账号列表 | |
| 备份 / 恢复 | 写进备份卷，重建容器不会丢。恢复时填列表里的名字即可；它把归档里有、数据库里没有的补上，已有的一概不覆盖，所以它不能把实例回滚 |
| 自检 | `dtk diagnose`，六步那个 |
| 日志 | 每个服务各自的最后若干行 |
| 重启 | 先应用 `.env` 和 compose 的改动（`up -d`），再重启应用服务 —— 不动 postgres 和 redis |
| 设置 | 任何运行时设置，包括 `retention.*`，改完不用重启。名字留空列出全部；新值按原样读入（空格保留），确认之后从标准输入喂进去 |
| 释放磁盘空间 | **只针对这份安装**、没有任何东西在用的镜像，Docker 的构建缓存可选 |
| 开启或关闭转写 | 给已经装好的实例用。打开：走和安装时一样的模型步骤，再处理镜像、`.env`，然后重建容器；起来后不健康就再关掉。关闭：删掉容器，模型留在磁盘上。`dtkctl` 里只动 `--profile transcriber` 这一个参数 |

「释放磁盘空间」值得说一句。容器日志已经被 compose 文件按每服务 10 MB × 3 封顶了，整套栈
的日志过不了 180 MB 左右。真正没有上限、会一直涨的是旧的镜像 tag 和 buildx 缓存；
而这里的清理只针对这份安装 —— 直接 `docker image prune -a` 会把同一台机器上
别的栈的镜像一起端掉。它只列出没有任何容器在用、这份安装的配置里也没写到的镜像，所以
用 `down` 停掉的栈也留得住它的浏览器镜像；本机构建的镜像，只有 Compose 标明是为这个项目
构建的才会列出来。构建缓存是 Docker 的，整台机器构建的东西共用它，提问时也会这么说。

**停止或移除**分三档：停掉但什么都不删、停掉并删数据卷、以及在此基础上连安装目录
一起删。「停掉」用的是 `stop` 而不是 `down`：容器还在，所以下次运行还找得到这份安装，
`./dtkctl up -d` 就能再启动。后两档要求打出 `delete` 而不是 y/n，并且会先告诉你怎么把
备份拷出来（`./dtkctl cp api:/var/lib/dtk/backups ./backups`）；删栈失败就停在那里，
别的什么都不删，删目录那一步还会拒绝任何看着不像安装的路径。选「返回」回到菜单。

加 `--manage` 可以直接进这个菜单。

## 它会问什么

七个问题，每个都有默认值，回车即可。任何一问按 Ctrl-C 都会让脚本停下，而不是取了默认值
接着往下走。

1. **装到哪里。** root 是 `/opt/dtk`，否则 `~/dtk`。
2. **发布在哪个地址和端口。** 默认 `127.0.0.1:8000`。选 `0.0.0.0` 会给你警告，
   因为那等于把一个不加密的管理后台放到网络上。
3. **要不要浏览器容器。** 它自己铸造游客身份，你就不用手动粘 Cookie；代价是大约
   4 GiB 内存和一个要在本机构建的 6 GB 镜像。内存不到 6 GiB 时建议关，
   加了 `--yes` 时一律不建。它安装 `docker/compose.yml` 指定的那个 commit 的 CloakBrowser，
   也就是这份代码的浏览器依赖验证过的版本，并且不把它写进 `.env`，所以每次升级它都跟着代码走。
   镜像里没有浏览器的老实例，在菜单里选「升级」（或「重装」）就会装上。
4. **要不要媒体下载。** 很小很省，默认开。
5. **要不要离线转写。** 在这台机器上把已存的视频转成文字：约 1 GiB 内存，转写时占两个核，
   一个 1.1 GB 的镜像和 500 MB 的模型。默认关；加了 `--yes` 时也关，除非设了
   `DTK_ENABLE_TRANSCRIBER=1`。没开媒体下载（它转写的就是下载存下的视频）或只有一个核时
   不提供这个选项；内存不到 4 GiB 时选“要”，会提醒你配上 swap。选了“要”，它会接着问模型
   放在哪——见下文。
6. **用已发布镜像还是从源码构建。** 默认用已发布的。
7. **要不要把代码切到正在安装的 Release**，前提是那个目录里已经有一份本项目的代码。

## 它做了什么

- 识别发行版，并给出对应的 Docker 安装方式。覆盖 Debian、Ubuntu 及其衍生版；
  RHEL、CentOS、Rocky、AlmaLinux、Fedora、Oracle、Amazon Linux；openSUSE 和
  SLES；Arch 和 Manjaro；Alpine；NixOS、Void、Gentoo。macOS 指向 Docker
  Desktop，WSL 指向 Windows 那一侧。
- 遇到 32 位或者非 x86_64 / arm64 的架构就直说，不瞎猜。
- **装的是 Release，不是 main。** 它克隆 `main`（完整历史，约 14 MB），再以分离状态
  检出最新 Release 的 tag，因为 main 总是跑在已发布镜像前面。`.env` 里写的
  `DTK_IMAGE_TAG` 是那个版本号（比如 `5.1.6`），而不是 `latest`。
- **按 Docker 能用的资源缩放上限** —— 在 Docker Desktop 或 OrbStack 上那是虚拟机，
  不是外面那台机器。`docker/compose.yml` 是按 4 vCPU / 8 GiB 写的。
  容器要的核数超过实际核数时 Docker 会**直接拒绝启动**，所以在更小的机器上，
  脚本会生成一份合身的 `compose.host.yml`：没有哪个 CPU 上限超过本机的核数，`migrate`
  和转写也算在内；每个被调低的 `mem_limit` 都带着跟它一致的 `memswap_limit`；小机器上
  还会往 `.env` 写一个低于 redis 新上限的 `DTK_REDIS_MAXMEMORY`。要被重写的
  `compose.host.yml` 如果和新内容不一样，会留成 `compose.host.yml.old`。
- 所有密钥用 `openssl rand` 生成，`.env` 在 `umask 077` 下写出，从不打印任何一个。
- 生成一个 `dtkctl` 包装脚本，替你带上那三个每次都得对的参数 —— 项目名、两个
  compose 文件，以及 `COMPOSE_ENV_FILES`；没有最后这个，Compose 会去读
  `docker/.env` 然后悄悄用默认值做插值。
- 这些文件一写完就跑一遍 Compose 自己的 `config -q` 检查，所以 Compose 不认的项目名
  或端口会按它本来的样子报出来，而不是变成一次拉取失败。
- **要转写又没有模型时，替你下载。** 模型目录默认是安装目录下的
  `models/funasr-onnx`。目录里已经有那十个运行时文件就直接用；装着别的东西、又没有
  `asr`、`vad`、`punc` 的目录会被拒绝；否则问你要不要从 ModelScope
  下载（约 500 MB），钉在 [准备 FunASR ONNX 模型](../documents/zh/18-funasr-models.md)
  验证过的 commit 上，每个文件都对一遍 SHA-256。权限只改那个目录和它下面的 `asr`、`vad`、
  `punc`，绝不对你输入的路径递归地改。模型下不下来，或者已发布的 transcriber
  镜像拉不到——转写功能之前的 Release 没有这个镜像——转写就保持关闭，安装的其他部分照常
  完成；之后可以在菜单里再打开。
- 拉镜像、跑迁移、启动、等所有服务报告健康，最后打印出创建第一个管理员的
  一次性链接。

## 它不会做什么

它只往你指定的那个目录里写东西，外加 Docker 自己的数据卷。不改那个目录之外的
任何文件，不加定时任务，不开防火墙端口，不问过你就不装任何东西。

它只在三件事上用 `sudo`，每一次都会先把命令原样打出来：你答应之后安装 Docker、
启动 Docker 服务、以及创建那个需要 root 才能建的安装目录。它**不会**把你加进
`docker` 组 —— 在大多数机器上那基本等同于给出 root 权限，所以它把命令告诉你，
由你自己决定。

它拒绝装到 `/`、`/etc`、`/usr`、`/var`、`/home` 这类目录，拒绝装进别的项目的
git 仓库，也拒绝装进一个已经有东西的目录。

它不会在旧安装的数据上开一份新安装。Docker 里已经有这个项目名的数据卷、而目录里又没有
`.env` 时，它会停下来讲清原因 —— 那个数据库只有旧安装的 `.env` 才打得开，新安装会生成
新的口令 —— 并给出三条路：去旧安装所在的地方管理它、换一个 `DTK_PROJECT` 装在它旁边，
或者删掉那些卷，这一条它会把确切的 `docker volume rm` 命令打出来。

## 选项

```
--yes, -y   全部取默认值。发布在 127.0.0.1:8000，开下载器，不建浏览器容器，
            不开转写。
--check     只探测系统并打印将会发生什么，然后停下。不改任何东西 ——
            适合先看看它对你这台机器的判断。
--manage    直接进入已有安装的管理菜单。
--upgrade [VERSION]
            升级已有的安装，什么都不问：升到最新的 Release，或者升到 VERSION
            （5.1.6、5.1、latest 或 sha-<commit>）。./dtkctl upgrade 跑的就是它。
--no-backup 配合 --upgrade：跳过迁移之前的那次备份。
--help, -h  上面这些，更短的版本。
```

每个答案都能用环境变量预设，这才让 `--yes` 成为真正的无人值守安装：

```bash
DTK_INSTALL_DIR=/srv/dtk DTK_BIND_PORT=9000 DTK_ENABLE_BROWSER=1 \
  bash install.zh.sh --yes
```

预设值和当场输入的答案一样要过校验，加不加 `--yes` 都是：端口或路径不对，脚本会停下并
指明是哪一个。而它拉起来的栈用的是写进 `.env` 的值：留在 shell 里的 `DTK_BIND_HOST`、
`DTK_BIND_PORT`、`DTK_TRANSCRIBER_MODEL_PATH`、`CLOAKBROWSER_COMMIT` 和 `DTK_IMAGE_TAG`
不会盖过它们。

`DTK_VERSION` 指定装哪个 Release：`5.1.6`、`5.1`（5.1.x 里最新的）、`latest`（默认）
或 `sha-<40 位 commit>`。检出的就是它的 tag（`sha-` 构建则是那个 commit）。
`DTK_REPO_URL` 让它从镜像仓库而不是 GitHub 克隆；之后的 Release tag 也从那个镜像取。
`DTK_GH_PROXY` 效果一样，给的是加在 GitHub 地址前的 `https://` 前缀。镜像经手整个安装和之后的
每一次升级，所以克隆之前会先问你，详见
[用镜像克隆，以及它的风险](../documents/zh/02-installation.md#用镜像克隆以及它的风险)。

转写用两个：`DTK_ENABLE_TRANSCRIBER=1` 打开它，`DTK_TRANSCRIBER_MODEL_PATH` 指定模型在哪
或该放哪。两个都设了再加 `--yes`，没有模型的目录会由那个带校验的下载填上。

`CLOAKBROWSER_COMMIT` 为浏览器镜像换一个 CloakBrowser 版本；不设的话，镜像跟着
`docker/compose.yml` 走。必须是完整的 40 位 commit，而且它会写进 `.env`，升级不会动它，
浏览器也就不再跟着代码走了；要设就有意识地设。

`DTK_PROJECT` 指定 Compose 项目名（默认 `dtk`）。设成别的值，就能在同一台机器上
跑第二套完全独立的栈。规则是 Compose 自己的：小写字母、数字、`-` 和 `_`，
以字母或数字开头。

## 前置要求

`bash`、`git`、`curl`，以及 `openssl` 或 `/dev/urandom` 二者之一 —— 不需要 `python3`。
Alpine 默认 bash 和 git 两样都没有：`apk add bash git curl`。

Docker Compose **2.24 或更新** —— compose 文件用到了长格式的 `env_file:` 和
`COMPOSE_ENV_FILES`，更老的版本失败的样子会让你以为是这个项目有问题。

## 在中国大陆安装

先换源。安装过程要从 Docker Hub 拉镜像，构建浏览器镜像还要访问 GitHub、PyPI 和
浏览器 CDN。转写模型来自 ModelScope，在大陆通常很快。没有镜像加速器的话，拉镜像那步
通常会超时，看起来就像项目坏了。GitHub 本身慢的话，`DTK_REPO_URL` 可以让它从本仓库的
镜像克隆，`DTK_GH_PROXY` 则是给一个镜像前缀。默认仍是 GitHub，用镜像之前会先问你。
详见 [中国大陆的网络准备](../documents/zh/02-installation.md#中国大陆的网络准备)。
