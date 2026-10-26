#!/usr/bin/env bash
#
# Douyin_TikTok_Download_API v5 —— 引导式 Docker 部署脚本。
#
#   curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.zh.sh -o install.zh.sh
#   less install.zh.sh       # 先读一遍，你马上要运行它
#   bash install.zh.sh
#
# 只支持 Docker。不用 Docker 就意味着你要自己在宿主机上装带 TimescaleDB 扩展的
# PostgreSQL、Redis、Python 和 Node —— 那是一篇文档能做的事，不是一个脚本：
# documents/zh/02-installation.md。
#
# 这个脚本会做什么、不会做什么
# ----------------------------
# 它只往你指定的那个目录里写东西，外加 Docker 自己的数据卷。它不会改那个目录之外
# 的任何文件，不会加定时任务，不会开防火墙端口，也不会在没问过你之前装任何东西。
#
# 它只在三种情况下用 sudo：安装 Docker（前提是你答应）、启动 Docker 服务、以及
# 创建那个需要 root 权限才能建的安装目录。这三件事都会先问你，并且把要执行的命令
# 原样打出来。
#
# 英文版是同目录下的 install.sh。两份结构保持一致，只有文案不同。

set -euo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------ 常量 --

# DTK_REPO_URL 让克隆走一个镜像。有了代码目录之后，发布版本都从它自己的 origin 上找，
# 所以之后也一直走那个镜像。
# DTK_GH_PROXY 给下面两个 GitHub 地址统一加一个镜像前缀，默认不加。REPO_RAW 在这里
# 从来不被下载，只出现在提示信息建议的下载命令里，所以前缀真正改变的是克隆地址——
# 设了 DTK_REPO_URL 时克隆地址以它为准。经手克隆的一方等于经手整个安装，所以只用你
# 信得过的镜像：只放行本仓库的自建中转（比如 Cloudflare Workers）最干净。
# 末尾带不带 / 都行。
gh_proxy="${DTK_GH_PROXY:-}"
while [ "$gh_proxy" != "${gh_proxy%/}" ]; do gh_proxy="${gh_proxy%/}"; done
# 只认 https，且不能带 shell 语法：REPO_RAW 会写进生成的 dtkctl 里一个双引号字符串，
# 跟下面的 DTK_PROJECT 是同一个理由。不带协议的前缀以前一路走到 git 才报错
# （"protocol 'gh-proxy.com/https' is not supported"），http:// 的则是明文克隆，
# 路上谁都能改。
if [ -n "$gh_proxy" ]; then
  case "${gh_proxy#https://}" in
    "$gh_proxy"|""|[![:alnum:]]*|*[![:alnum:]._~:/?=%+@-]*)
      printf '错误：DTK_GH_PROXY 必须是 https:// 开头的地址，比如 https://mirror.example/，只能有字母、数字和 . _ ~ : / ? = %% + @ -。\n' >&2
      exit 1 ;;
  esac
fi
readonly GH_PROXY="${gh_proxy:+$gh_proxy/}"
readonly GITHUB_REPO_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git"
readonly REPO_URL="${DTK_REPO_URL:-${GH_PROXY}${GITHUB_REPO_URL}}"
readonly REPO_RAW="${GH_PROXY}https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main"
readonly DOCKER_INSTALL_URL="https://get.docker.com"
readonly COMPOSE_INSTALL_URL="https://docs.docker.com/compose/install/linux/"
readonly DOCS_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/zh/02-installation.md"

# Compose 从 2.24 才有长格式的 `env_file:` 和 COMPOSE_ENV_FILES，而
# docker/compose.yml 两个都用到了。更老的版本会以一种看起来像"这个项目有问题"
# 的方式失败。
readonly COMPOSE_MIN="2.24"

# transcriber 加载的三个 FunASR 模型，钉在 documents/zh/18-funasr-models.md
# 验证过的 commit 上，外加 sidecar 会读的每个文件的 SHA-256。commit 不会像
# `master` 那样在下载过程中变掉；有了哈希，截断或被替换的文件在这里就报错，
# 而不是变成一个永远起不来的 sidecar。
readonly MODEL_HOST="https://www.modelscope.cn/models/iic"
readonly MODEL_REPOS=(
  "asr|speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx|7162433de474912574f9d03122263776db5b046a"
  "vad|speech_fsmn_vad_zh-cn-16k-common-onnx|a158155ef9e81f405c052f40b6d1ad43b87e6215"
  "punc|punc_ct-transformer_zh-cn-common-vocab272727-onnx|8f239ff78c6267c4d859233e7eb3bbdb68c61824"
)
readonly MODEL_FILES=(
  "asr/model_quant.onnx|c3a06538f867c8e3d9bb9c9e1b44b126a50d732862f6c428b1b5896ee9be65c5"
  "asr/config.yaml|bfd424b9f61846aac63591e96fe24d66b35f4643bddcb4b9bb759db8f987ab79"
  "asr/am.mvn|29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5"
  "asr/tokens.json|2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127"
  "vad/model_quant.onnx|5289eb2aa3c9af2d7a4284bcfa7c3ceb81d360814ed4203239b6c5d0569da8a1"
  "vad/config.yaml|2ef334f2d7776edd86ed696296774f87550206c256bfabc597872ad861831589"
  "vad/am.mvn|6820fef9687708c4fc3fab2530179c8fcea6262daa25514380056cd8f6eb1754"
  "punc/model_quant.onnx|e6cd8399bf7d0e75f8d9af4a107310e1968ecab1d50135e765b8f0265b27a83d"
  "punc/config.yaml|a56ec10925b06fa976ad51af373396be2b13e1eb8dc62a5426b5adebaba7071d"
  "punc/tokens.json|c960ab87bccea4aa15cf49a59f71973c2c330b46668048cd8da253749ec71ee3"
)
readonly TRANSCRIBER_IMAGE="evil0ctal/douyin_tiktok_download_api-transcriber"

# 这个文件在一份安装的代码目录里叫什么。dtkctl 升级时跑的是那一份，升级在代码
# 更新之后也会交给它接着做。英文脚本写的是它自己的名字，所以用中文装的实例一直是中文。
readonly SCRIPT_NAME="install.zh.sh"

# 这个项目往 .env 里写过的每一个 CloakBrowser commit：5.1.4 到 5.1.6 安装时写的是
# 0.6.3 的，在那之前 .env.example（手动安装会照抄它）带的是 0.5.10 的。写死的那份让
# 一个实例在代码的 requirements 已经换到 Playwright 1.64 之后还停在 0.5.10，所以升级时
# 会把它们拿掉（settle_browser_pin）。这个列表是封闭的：现在已经没有任何地方会把默认值
# 写进 .env，以后换 pin 也不用往这里加。
readonly CLOAKBROWSER_WRITTEN_PINS="f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce fbf005bc199920b592df17ac4d489324e29d56d8"

# 可以覆盖，这样试跑时不会碰到真正在用的 `dtk` 那套。做了校验，因为这个值会被写进
# 生成的 dtkctl 里 —— 不是纯名字的东西到了那里就变成 shell 语法了。用的是 Compose
# 自己的规则，不是更宽松的一套：`MyDtk` 在这里能通过，却在第一次 pull 时失败，
# 错误还怪到了镜像仓库头上。
PROJECT="${DTK_PROJECT:-dtk}"
case "$PROJECT" in
  ''|[!a-z0-9]*|*[!a-z0-9_-]*)
    printf '错误：DTK_PROJECT 只能是小写字母、数字、短横线或下划线，并且以字母或数字开头。\n' >&2
    exit 1 ;;
esac

# ------------------------------------------------------------------ 输出 --

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''
fi

step() { printf '\n%s==>%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
info() { printf '    %s\n' "$*"; }
dim()  { printf '    %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }
ok()   { printf '    %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '    %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '\n%s错误：%s %s\n\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ------------------------------------------------------------------ 输入 --
#
# 管道运行时怎么读输入，是粗糙的安装脚本最常翻车的地方：`curl … | bash` 的时候，
# 标准输入就是脚本本身，`read` 会把脚本自己剩下的行吃掉。所以这里的输入一律显式
# 从终端读；如果压根没有终端，脚本会明说，而不是对"服务发布到哪个地址"这种问题
# 默默取默认值。

TTY_IN=""
if [ -t 0 ]; then
  TTY_IN="/dev/stdin"
elif [ -r /dev/tty ] && { : >/dev/tty; } 2>/dev/null; then
  TTY_IN="/dev/tty"
fi

ASSUME_YES=0
CHECK_ONLY=0
MANAGE_ONLY=0
UPGRADE_ONLY=0
UPGRADE_TARGET=""
NO_BACKUP=0

# 凡是建在安装目录之外的东西都登记在这里，这样中途 Ctrl-C 也不会留下垃圾。
TMP_FILES=()
# 升级已经挪动了代码目录、但还没切换整套服务时会设上它：这时离开脚本 —— 出错、
# 按 Ctrl-C —— 都会把代码目录和 .env 放回原样（undo_upgrade），这样说"什么都没改"
# 的时候，那就是真的。
UPGRADE_UNDO=""
# 关掉回显读过一次密码之后会设上它。在那次读取里按 Ctrl-C，曾经让终端之后输入的
# 任何东西都不再回显。
SECRET_PROMPTED=0
# 它必须以成功结束。脚本自然跑完时，退出码取最后执行的那条命令，也就是这个 trap；
# 没有登记任何文件时那条命令是 `[ -n "" ]`，于是每次不需要临时文件的安装都在
# 打印完「完成」之后报告失败。
cleanup() {
  local f
  if [ -n "$UPGRADE_UNDO" ]; then undo_upgrade; fi
  if [ "$SECRET_PROMPTED" = 1 ] && [ -n "$TTY_IN" ]; then stty echo <"$TTY_IN" 2>/dev/null || true; fi
  for f in "${TMP_FILES[@]:-}"; do
    if [ -n "$f" ]; then rm -f "$f"; fi
  done
  return 0
}
trap cleanup EXIT
# INT 和 TERM 会结束脚本。它们以前只跑 cleanup、别的什么都不做，而一个会返回的
# trap 会让脚本接着往下走：在「启用浏览器容器吗？[Y/n]」那里按 Ctrl-C，取了默认值，
# 然后接着去构建一个 6 GB 的镜像。
trap 'exit 130' INT
trap 'exit 143' TERM

# 从终端读一行。遇到 EOF 返回空而不是让脚本失败，这样终端被关掉时会落到默认值。
read_line() {
  local reply=""
  if [ -n "$TTY_IN" ]; then
    IFS= read -r reply <"$TTY_IN" || reply=""
  fi
  printf '%s' "$reply"
}

# ask_yes_no "问题" "y|n"  —— 是返回 0，否返回 1
ask_yes_no() {
  local prompt="$1" default="$2" hint reply
  if [ "$default" = "y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  if [ "$ASSUME_YES" = 1 ] || [ -z "$TTY_IN" ]; then
    [ "$default" = "y" ]
    return
  fi
  while true; do
    printf '    %s %s ' "$prompt" "$hint" >&2
    reply="$(read_line)"
    reply="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$reply" in
      "")        [ "$default" = "y" ]; return ;;
      y|yes)     return 0 ;;
      n|no)      return 1 ;;
      *)         warn "请回答 y 或 n。" ;;
    esac
  done
}

# ask_value "问题" "默认值" [校验函数]
ask_value() {
  local prompt="$1" default="$2" validator="${3:-}" reply
  if [ "$ASSUME_YES" = 1 ] || [ -z "$TTY_IN" ]; then
    # 预设值和手打的回答守同一条规则。--yes 时没有人能重新输入，而一个不对的端口
    # 或路径，以前要到第一次 `docker compose pull` 才露出来，还是一条讲镜像仓库的消息。
    if [ -n "$validator" ] && ! "$validator" "$default"; then
      die "'${default}' 不能用作「${prompt}」的回答，而这里没有人可以问。改掉预设值再跑一次。"
    fi
    printf '%s' "$default"
    return
  fi
  while true; do
    printf '    %s %s[%s]%s ' "$prompt" "$C_DIM" "$default" "$C_RESET" >&2
    reply="$(read_line)"
    reply="$(printf '%s' "$reply" | tr -d '[:space:]')"
    [ -z "$reply" ] && reply="$default"
    if [ -z "$validator" ] || "$validator" "$reply"; then
      printf '%s' "$reply"
      return
    fi
  done
}

# 原样读一行，只去掉两头的空白。ask_value 会去掉所有空格，这对端口或路径合适，
# 对一个设置项却是破坏：`GET /api/v1/x` 被存成了 `GET/api/v1/x`，校验还让它过了。
ask_line() {
  local prompt="$1" reply
  [ -n "$TTY_IN" ] && [ "$ASSUME_YES" = 0 ] || return 0
  printf '    %s ' "$prompt" >&2
  reply="$(read_line)"
  reply="${reply#"${reply%%[![:space:]]*}"}"
  reply="${reply%"${reply##*[![:space:]]}"}"
  printf '%s' "$reply"
}

valid_port() {
  case "$1" in
    ''|*[!0-9]*) warn "端口得是数字。"; return 1 ;;
  esac
  if [ "$1" -lt 1 ] || [ "$1" -gt 65535 ]; then
    warn "端口范围是 1 到 65535。"
    return 1
  fi
  return 0
}

valid_path() {
  case "$1" in
    /|"") warn "这不是一个该往里装东西的目录。"; return 1 ;;
    /*|~*|./*|../*) return 0 ;;
    *) warn "请用绝对路径，比如 /opt/dtk。"; return 1 ;;
  esac
}

# 展开开头的 ~。它从 `read` 进来时是字面字符，所以这里是对一个字符做模式匹配，
# 不是在尝试展开。
# shellcheck disable=SC2088
expand_tilde() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s' "$HOME/${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# 转成绝对路径，去掉结尾斜杠和 '..'。下面那张拒绝清单比的是字面名字，
# 只有当被比的路径已经是真实路径时，它才有意义。
absolute_path() {
  local path="$1"
  case "$path" in
    /*) : ;;
    *)  path="$PWD/$path" ;;
  esac
  # 自己解析，不要求目录已经存在 —— realpath -e 会要求。
  local out=""
  local IFS=/
  local part
  for part in $path; do
    case "$part" in
      ''|'.') : ;;
      '..')   out="${out%/*}" ;;
      *)      out="$out/$part" ;;
    esac
  done
  printf '%s' "${out:-/}"
}

have() { command -v "$1" >/dev/null 2>&1; }

# 不是 root 时用 sudo 执行一条命令，并且先把命令打出来。绝不套在一整段上，
# 只用在确实需要提权的那一条命令上。
as_root() {
  if [ "$(id -u)" = "0" ]; then
    "$@"
  else
    have sudo || die "这一步需要 root，而这台机器没有装 sudo：$(printf '%q ' "$@")"
    # 用 %q 而不是 "$*"：IFS 设成换行和制表符时，"$*" 会把命令的每个词各打在一行上。
    dim "sudo $(printf '%q ' "$@")"
    sudo "$@"
  fi
}

# ------------------------------------------------------------------ 探测 --

OS=""          # linux | macos | wsl | unsupported（不支持）
DISTRO=""      # debian、ubuntu、fedora、rhel、arch、alpine、opensuse …
DISTRO_LIKE="" # os-release 里的 ID_LIKE
DISTRO_VERSION="" # os-release 里的 VERSION_ID
DISTRO_NAME=""
ARCH=""
CPUS=""
MEM_MIB=""

detect_os() {
  local kernel
  kernel="$(uname -s 2>/dev/null || echo unknown)"
  case "$kernel" in
    Linux)
      OS="linux"
      # WSL 也是 Linux，但那里的 Docker 是 Windows 侧的 Docker Desktop，
      # 给"用包管理器装"的建议是错的。
      if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then OS="wsl"; fi
      if [ -r /etc/os-release ]; then
        # 放在子 shell 里 source，避免 os-release 的变量污染本脚本的命名空间
        # —— 它会设 NAME、VERSION 这类很常见的名字。
        # shellcheck disable=SC1091
        DISTRO="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
        # shellcheck disable=SC1091
        DISTRO_LIKE="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID_LIKE:-}")"
        # shellcheck disable=SC1091
        DISTRO_VERSION="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-}")"
        # shellcheck disable=SC1091
        DISTRO_NAME="$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-${NAME:-}}")"
      fi
      [ -n "$DISTRO_NAME" ] || DISTRO_NAME="Linux"
      ;;
    Darwin)
      OS="macos"
      DISTRO_NAME="macOS $(sw_vers -productVersion 2>/dev/null || true)"
      ;;
    FreeBSD|OpenBSD|NetBSD)
      OS="unsupported"; DISTRO_NAME="$kernel"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      OS="unsupported"; DISTRO_NAME="Windows (Git Bash)"
      ;;
    *)
      OS="unsupported"; DISTRO_NAME="$kernel"
      ;;
  esac

  ARCH="$(uname -m 2>/dev/null || echo unknown)"
  case "$ARCH" in
    x86_64|amd64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="arm64" ;;
  esac
}

# 这个发行版属于哪一系，用来给出对应的 Docker 安装建议。
distro_family() {
  local id="${DISTRO}" like=" ${DISTRO_LIKE} "
  case "$id" in
    debian|ubuntu|linuxmint|pop|raspbian|kali|neon|elementary|zorin|deepin|armbian|devuan) echo debian; return ;;
    rhel|centos|rocky|almalinux|fedora|ol|oracle|amzn|scientific|circle) echo rhel; return ;;
    opensuse*|sles|sled|suse) echo suse; return ;;
    arch|manjaro|endeavouros|garuda|cachyos|artix) echo arch; return ;;
    alpine) echo alpine; return ;;
    nixos) echo nixos; return ;;
    void) echo void; return ;;
    gentoo) echo gentoo; return ;;
  esac
  case "$like" in
    *" debian "*|*" ubuntu "*) echo debian; return ;;
    *" rhel "*|*" fedora "*|*" centos "*) echo rhel; return ;;
    *" suse "*|*" opensuse "*) echo suse; return ;;
    *" arch "*) echo arch; return ;;
  esac
  echo unknown
}

detect_resources() {
  CPUS="$(
    { command -v nproc >/dev/null 2>&1 && nproc; } \
      || sysctl -n hw.ncpu 2>/dev/null \
      || getconf _NPROCESSORS_ONLN 2>/dev/null \
      || echo 1
  )"
  case "$CPUS" in ''|*[!0-9]*) CPUS=1 ;; esac

  if [ -r /proc/meminfo ]; then
    MEM_MIB="$(awk '/^MemTotal:/ {printf "%d", $2/1024; exit}' /proc/meminfo 2>/dev/null || echo 0)"
  elif have sysctl; then
    local bytes
    bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    MEM_MIB=$(( bytes / 1024 / 1024 ))
  else
    MEM_MIB=0
  fi
  case "$MEM_MIB" in ''|*[!0-9]*) MEM_MIB=0 ;; esac
}

# Docker 守护进程能分出去的资源，资源上限要按它来定。在 Docker Desktop、OrbStack
# 或远程 DOCKER_HOST 上，那是一台虚拟机，不是这台宿主机：一台 16 核的 Mac 前面挂着
# 一个 2 CPU 的虚拟机，结果没写覆盖文件，browser-rpc 要了 4.0 个 CPU，Docker 直接拒绝。
detect_docker_resources() {
  local line cpus bytes mib
  line="$(docker info --format '{{.NCPU}} {{.MemTotal}}' 2>/dev/null || true)"
  cpus="${line%% *}"
  bytes="${line##* }"
  case "$cpus" in ''|*[!0-9]*) return 0 ;; esac
  case "$bytes" in ''|*[!0-9]*) return 0 ;; esac
  [ "$cpus" -gt 0 ] && [ "$bytes" -gt 0 ] || return 0
  mib=$(( bytes / 1024 / 1024 ))
  if [ "$UPGRADE_ONLY" = 0 ] && { [ "$cpus" != "$CPUS" ] || [ $(( mib - MEM_MIB )) -gt 256 ] || [ $(( MEM_MIB - mib )) -gt 256 ]; }; then
    ok "Docker 在这里能用 ${cpus} 个 CPU 和 $(( mib / 1024 )).$(( (mib % 1024) * 10 / 1024 )) GiB 内存，整套服务就按这个来定规格。"
  fi
  CPUS="$cpus"
  MEM_MIB="$mib"
}

# ---------------------------------------------------------------- docker --

# "2.29.7" -> 2029007，这样不用 sort -V 也能按整数比较版本。
version_key() {
  printf '%s' "$1" | awk -F'[^0-9]+' '{printf "%d%03d%03d", $1+0, $2+0, $3+0}'
}

check_docker_present() {
  have docker
}

check_docker_running() {
  docker info >/dev/null 2>&1
}

check_compose() {
  if docker compose version >/dev/null 2>&1; then
    local raw key
    raw="$(docker compose version --short 2>/dev/null || echo 0)"
    key="$(version_key "$raw")"
    if [ "$key" -lt "$(version_key "$COMPOSE_MIN")" ]; then
      warn "当前 Compose 是 ${raw}，这套栈需要 $COMPOSE_MIN 或更新的版本。"
      info "装一个更新的 Compose 插件后再跑一次：$COMPOSE_INSTALL_URL"
      return 1
    fi
    ok "Docker Compose $raw"
    return 0
  fi
  if have docker-compose; then
    warn "检测到的是老的独立版 docker-compose（v1）。"
    info "这套栈用到了 Compose v2 的特性。请安装 Compose 插件："
    info "  $COMPOSE_INSTALL_URL"
    return 1
  fi
  warn "没有装 Docker Compose v2。"
  info "安装 Compose 插件：$COMPOSE_INSTALL_URL"
  return 1
}

# 从发行版自己的软件包装 Docker 和 $COMPOSE_MIN 或更新的 Compose；包不够新的，
# 就指向 Docker 介绍它自家软件源的页面。2026 年 10 月逐个核对过各发行版的软件源：
# Debian 12 只有 Compose v1，openSUSE Leap 15.6 的 Compose 是 2.22，RHEL 系
# 里叫 `docker` 的是 podman 的兼容层。这里以前对整个 RHEL 系都写 `dnf install docker docker-compose-plugin`，
# 而那个插件包只在 Docker 自家的软件源里有，所以 dnf 会整行拒绝——连 Fedora
# 也不例外，它其实两样都有，只是包名不同。
docker_install_hint() {
  local docs="https://docs.docker.com/engine/install"
  case "$DISTRO" in
    debian)
      case "$DISTRO_VERSION" in
        1[3-9]|[2-9][0-9]) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose docker-buildx" ;;
        *) printf '%s' "见 $docs/debian/" ;;
      esac
      return ;;
    raspbian) printf '%s' "见 $docs/raspberry-pi-os/"; return ;;
    fedora)   printf '%s' "sudo dnf install -y moby-engine docker-compose docker-buildx"; return ;;
    rhel)     printf '%s' "见 $docs/rhel/"; return ;;
    amzn)     printf '%s' "sudo dnf install -y docker，然后装 Compose 插件：$COMPOSE_INSTALL_URL"; return ;;
  esac
  case "$(distro_family)" in
    debian)
      case " $DISTRO $DISTRO_LIKE " in
        *" ubuntu "*) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 docker-buildx" ;;
        *) printf '%s' "见 $docs/debian/" ;;
      esac ;;
    rhel) printf '%s' "见 $docs/centos/" ;;
    suse)
      case "$DISTRO_VERSION" in
        1[0-5]|1[0-5].*) printf '%s' "sudo zypper install -y docker，然后装 Compose 插件：$COMPOSE_INSTALL_URL" ;;
        *) printf '%s' "sudo zypper install -y docker docker-compose docker-buildx" ;;
      esac ;;
    arch)   printf '%s' "sudo pacman -S --needed docker docker-compose docker-buildx" ;;
    alpine) printf '%s' "sudo apk add docker docker-cli-compose docker-cli-buildx && sudo rc-update add docker default" ;;
    nixos)  printf '%s' "在 configuration.nix 里加 virtualisation.docker.enable = true;" ;;
    void)   printf '%s' "sudo xbps-install -S docker docker-compose" ;;
    gentoo) printf '%s' "sudo emerge app-containers/docker app-containers/docker-compose" ;;
    *)      printf '%s' "见 $docs/" ;;
  esac
}

git_install_hint() {
  case "$(distro_family)" in
    debian) printf '%s' "sudo apt-get install -y git" ;;
    rhel)   printf '%s' "sudo dnf install -y git" ;;
    suse)   printf '%s' "sudo zypper install -y git" ;;
    arch)   printf '%s' "sudo pacman -S --needed git" ;;
    alpine) printf '%s' "sudo apk add git" ;;
    *)      printf '%s' "你的包管理器" ;;
  esac
}

# 官方的便捷安装脚本支不支持这个发行版。它看的是 os-release 里的 ID，不是发行版
# 系别：只认 ubuntu、debian、raspbian、centos、rhel、rocky、fedora，其余的
# （SUSE、AlmaLinux、Amazon Linux 都在内）一律报 "Unsupported distribution"，
# 有些衍生版还会被它对到一个并非其来源的上游版本。这里以前按系别判断，
# 在整个 SUSE 系上都提议跑它，而它在那里从来没成功过。
convenience_script_supported() {
  case "$DISTRO" in
    ubuntu|debian|raspbian|centos|rhel|rocky|fedora) return 0 ;;
    *) return 1 ;;
  esac
}

offer_docker_install() {
  case "$OS" in
    macos)
      die "没有装 Docker。
    macOS 上请装 Docker Desktop 或 OrbStack，启动它，然后再跑一次这个脚本：
      https://www.docker.com/products/docker-desktop/
    用 Homebrew：brew install --cask docker"
      ;;
    wsl)
      die "这个 WSL 发行版里没有可用的 Docker。
    请在 Windows 上装 Docker Desktop，并为这个发行版打开 WSL 集成，
    然后再跑一次：
      https://docs.docker.com/desktop/wsl/"
      ;;
    unsupported)
      die "这个脚本只在 Linux 上安装 Docker，macOS 上则期望你用 Docker Desktop。
    在 $DISTRO_NAME 上请自行安装 Docker，然后再跑一次：
      https://docs.docker.com/engine/install/"
      ;;
  esac

  warn "没有装 Docker。"
  info ""
  info "两条路，都需要 sudo。"
  info ""
  info "  1. 手动安装："
  dim  "     $(docker_install_hint)"
  if convenience_script_supported; then
    info ""
    info "  2. Docker 官方的安装脚本，这个脚本可以替你跑："
    dim  "     curl -fsSL $DOCKER_INSTALL_URL | sudo sh"
    info ""
    info "     它会从 Docker 公司下载一个 shell 脚本并以 root 身份执行。"
    info "     这是官方的那一份，也是 Docker 自己的文档让你用的，但它终究是"
    info "     以 root 跑远程代码，所以这件事由你决定，而不是默认就做。"
    info ""
    if ask_yes_no "现在就跑 Docker 官方安装脚本吗？" "n"; then
      step "正在安装 Docker"
      local tmp
      tmp="$(mktemp -t dtk-docker-install.XXXXXX)" || die "创建临时文件失败。"
      TMP_FILES+=("$tmp")
      # 先下载再执行：直接管道进 shell 的东西没法检查，而且下载被截断就会变成
      # 一个执行到一半的脚本。
      if ! curl -fsSL --proto '=https' --tlsv1.2 "$DOCKER_INSTALL_URL" -o "$tmp"; then
        rm -f "$tmp"
        die "下载 Docker 安装脚本失败。请手动安装 Docker，然后再跑一次。"
      fi
      if [ ! -s "$tmp" ]; then
        rm -f "$tmp"
        die "下载到的 Docker 安装脚本是空的。请手动安装 Docker，然后再跑一次。"
      fi
      info "已下载到 ${tmp}（$(wc -c <"$tmp" | tr -d ' ') 字节）。"
      as_root sh "$tmp" || { rm -f "$tmp"; die "Docker 安装脚本执行失败，请看上面它自己的输出。"; }
      rm -f "$tmp"
      ok "Docker 安装完成。"
    else
      die "请按上面的方法装好 Docker，然后再跑一次这个脚本。"
    fi
  else
    info ""
    info "  2. Docker 官方安装脚本不支持 ${DISTRO_NAME}，所以请按上面的"
    info "     方法安装。"
    die "装好 Docker 之后再跑一次这个脚本。"
  fi
}

ensure_docker_running() {
  if check_docker_running; then
    return 0
  fi

  if [ "$OS" = "macos" ]; then
    die "Docker 装了但没在跑。请启动 Docker Desktop（或 OrbStack），
    等它显示已经运行起来，然后再跑一次这个脚本。"
  fi

  warn "Docker 装了，但连不上。"
  if have systemctl && ask_yes_no "现在启动 Docker 服务吗？" "y"; then
    as_root systemctl enable --now docker || true
    sleep 2
  elif have rc-service && ask_yes_no "现在启动 Docker 服务吗？" "y"; then
    as_root rc-update add docker default || true
    as_root rc-service docker start || true
    sleep 2
  fi

  if check_docker_running; then
    ok "Docker 已经在运行。"
    return 0
  fi

  # 另一种常见情况：守护进程活着，但当前用户没权限和它说话。要说清是这两种
  # 里的哪一种，而不是笼统地"连不上"。
  if [ "$(id -u)" != "0" ] && sudo -n docker info >/dev/null 2>&1; then
    die "Docker 在跑，但当前用户没权限访问它。
    把自己加进 docker 组，然后开一个新的登录 shell：
      sudo usermod -aG docker \"\$USER\"
      newgrp docker
    然后再跑一次这个脚本。（在这台机器上，进了 docker 组基本等同于拿到 root，
    所以这一步这个脚本不替你做。）"
  fi

  die "Docker 装了但没在跑，而且尝试启动也没成功。
    请按这个系统自己的方式把它启动起来，用 'docker info' 确认之后，
    再跑一次这个脚本。"
}

# -------------------------------------------------------------- 随机数 --

random_hex() {
  local bytes="$1"
  if have openssl; then
    openssl rand -hex "$bytes"
  elif [ -r /dev/urandom ] && have od; then
    od -An -tx1 -N "$bytes" /dev/urandom | tr -d ' \n'
  else
    die "没有可用的随机源（openssl 或 /dev/urandom），无法生成密钥。"
  fi
}

random_b64() {
  local bytes="$1"
  if have openssl; then
    # base64 里可能有 / 和 +，对 DTK_SECRET_KEY 无所谓，它不会出现在 URL 里。
    # 两个数据库口令用 hex，正是因为它们要拼进连接 URL。
    openssl rand -base64 "$bytes" | tr -d '\n'
  elif [ -r /dev/urandom ] && have base64; then
    head -c "$bytes" /dev/urandom | base64 | tr -d '\n'
  else
    die "没有可用的随机源（openssl 或 /dev/urandom），无法生成密钥。"
  fi
}

# ------------------------------------------------------------ .env 与 dtkctl --
#
# .env 和 dtkctl 由安装过程整份写出。之后的一切 —— 从菜单开启转写，或某一步失败
# 时把它关掉 —— 都只改一行，文件其余部分保持原样，因为可能有人手动改过。

env_value() {
  grep "^$1=" "$INSTALL_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

# 替换 KEY 那一行，没有就追加。经临时文件再 `cat >` 写回，而不是 `mv`，
# 这样 .env 的 inode、属主和 0600 权限都不变。
set_env_value() {
  local key="$1" value="$2" env_file="$INSTALL_DIR/.env" tmp
  tmp="$(mktemp)" || die "无法创建临时文件。"
  TMP_FILES+=("$tmp")
  if grep -q "^${key}=" "$env_file"; then
    awk -v key="$key" -v value="$value" \
      'index($0, key "=") == 1 { print key "=" value; next } { print }' "$env_file" >"$tmp"
  else
    cat "$env_file" >"$tmp"
    # 文件末尾没有换行的话，新的键会粘到最后一个值上。
    if [ -n "$(tail -c 1 "$env_file")" ]; then printf '\n' >>"$tmp"; fi
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  cat "$tmp" >"$env_file"
  rm -f "$tmp"
}

# 把 KEY 那一行换成一条说明它为什么被拿掉的注释，写法和 set_env_value 一样。
# 直接删掉的话，看 .env 的人找不到任何改动过的痕迹。
drop_env_value() {
  local key="$1" note="$2" env_file="$INSTALL_DIR/.env" tmp
  grep -q "^${key}=" "$env_file" 2>/dev/null || return 0
  tmp="$(mktemp)" || die "无法创建临时文件。"
  TMP_FILES+=("$tmp")
  awk -v key="$key" -v note="$note" '
    index($0, key "=") == 1 { if (!done) print "# " note; done = 1; next }
    { print }' "$env_file" >"$tmp"
  cat "$tmp" >"$env_file"
  rm -f "$tmp"
}

# 在 dtkctl 的 compose 那一行上原地加上或去掉一个 --profile。
set_ctl_profile() {
  local name="$1" state="$2" flag tmp
  flag=" --profile $name"
  tmp="$(mktemp)" || die "无法创建临时文件。"
  TMP_FILES+=("$tmp")
  if [ "$state" = on ]; then
    # 单引号：这里的 "$@" 是要写进 dtkctl 的 awk 文本，不是要展开的变量。
    # shellcheck disable=SC2016
    awk -v flag="$flag" '
      /^exec docker compose / && index($0, flag) == 0 { sub(/ "\$@"$/, flag " \"$@\"") }
      { print }' "$CTL" >"$tmp"
  else
    awk -v flag="$flag" '
      /^exec docker compose / {
        at = index($0, flag)
        if (at) $0 = substr($0, 1, at - 1) substr($0, at + length(flag))
      }
      { print }' "$CTL" >"$tmp"
  fi
  cat "$tmp" >"$CTL"
  rm -f "$tmp"
  if [ "$state" = on ] && ! grep -qF -- "$flag" "$CTL"; then
    warn "没能把${flag} 加进 ${CTL}，请手动加到 compose 那一行上。"
  fi
}

# ------------------------------------------------------------------ 转写 --
#
# transcriber 是唯一一个数据不在镜像里的可选组件：三个模型，约 500 MB，容器以
# 只读方式挂载、从不自己下载。下面这些就是把它们弄到这台机器上，并证明字节是对的。

sha256_of() {
  if have sha256sum; then
    sha256sum "$1" | awk '{print $1}'
  elif have shasum; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    return 1
  fi
}

# sidecar 要读的每个文件都在，而且比 Git LFS 指针文件大。不比哈希：手动准备的
# 目录可能是上游更新的版本，能不能加载由 sidecar 自己的启动检查来判断。
models_present() {
  local root="$1" entry path size
  for entry in "${MODEL_FILES[@]}"; do
    path="${entry%%|*}"
    [ -s "$root/$path" ] || return 1
    case "$path" in
      *.onnx)
        # 指针文件大约 130 字节；这几个模型里最小的也有 500 KB。
        size="$(wc -c <"$root/$path" | tr -d ' ')"
        [ "$size" -gt 100000 ] || return 1
        ;;
    esac
  done
  return 0
}

# 容器以 UID 10001 读模型，而不是以运行本脚本的用户。一个只对属主开放的目录，
# 会让 transcriber 一直不健康，原因只写在那个容器自己的日志里。
models_readable() {
  [ -z "$(find "$1" -maxdepth 0 ! -perm -005 2>/dev/null)" ] \
    && [ -z "$(find "$1/asr" "$1/vad" "$1/punc" \( -type d ! -perm -005 \) -o \( -type f ! -perm -004 \) 2>/dev/null | head -1)" ]
}

model_repo_for() {
  local entry
  for entry in "${MODEL_REPOS[@]}"; do
    case "$entry" in
      "$1|"*) printf '%s' "${entry#*|}"; return 0 ;;
    esac
  done
  return 1
}

# 逐个文件从钉住的 commit 下载。已经在且哈希正确的文件会保留，所以断线后再试
# 一次，只会下载上次没下完的部分。
download_models() {
  local root="$1" entry path want origin repo commit tmp got progress="-sS"
  have curl || { warn "下载模型需要 curl，但这台机器没装。"; return 1; }
  if ! sha256_of /dev/null >/dev/null 2>&1; then
    warn "sha256sum 和 shasum 都没有，无法校验模型。"
    return 1
  fi
  if [ -t 2 ]; then progress="--progress-bar"; fi

  step "下载转写模型"
  info "约 500 MB，来自 ModelScope，用的是文档验证过的那几个 commit。"
  info "每个文件都先对过 SHA-256 才会保留。"
  mkdir -p "$root/asr" "$root/vad" "$root/punc" || { warn "无法创建 ${root}。"; return 1; }

  for entry in "${MODEL_FILES[@]}"; do
    path="${entry%%|*}"
    want="${entry##*|}"
    if [ -f "$root/$path" ] && [ "$(sha256_of "$root/$path")" = "$want" ]; then
      ok "${path}（已存在）"
      continue
    fi
    origin="$(model_repo_for "${path%%/*}")"
    repo="${origin%%|*}"
    commit="${origin##*|}"
    tmp="$root/$path.part"
    TMP_FILES+=("$tmp")
    info "$path"
    if ! curl -fL --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 20 "$progress" \
        -o "$tmp" "$MODEL_HOST/$repo/resolve/$commit/${path#*/}"; then
      rm -f "$tmp"
      warn "${path} 下载失败。"
      return 1
    fi
    got="$(sha256_of "$tmp")"
    if [ "$got" != "$want" ]; then
      rm -f "$tmp"
      warn "${path} 与校验和不符，没有保留。"
      warn "应为 ${want}，实际为 ${got}。"
      return 1
    fi
    mv -f "$tmp" "$root/$path"
  done
  open_models "$root" || { warn "无法让容器读到 ${root}。"; return 1; }
  ok "十个文件都已校验，位于 ${root}"
}

# 让容器的 UID 10001 能读：目录本身，以及这个脚本往里放东西的那三个 —— 绝不对
# 用户给的那个目录用 -R。「模型目录？」曾经收到过 ~，`chmod -R a+rX ~` 让整个家目录
# 连同 .ssh 都变成了所有人可读。
open_models() {
  chmod a+rx "$1" && chmod -R a+rX "$1/asr" "$1/vad" "$1/punc"
}

# 只接受完整的 commit。tag 或分支会在钉住之后自己移动；不是十六进制的东西会变成
# 一个 Docker 拒收的构建参数，而且要等构建跑了好几分钟才报错。
valid_commit() {
  case "$1" in
    *[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ]
}

# .env 里没指定时，这份代码构建的 CloakBrowser commit：它的 docker/compose.yml 里的
# 默认值，docker/browser_rpc/requirements.txt 就是用它验证的。从代码目录里读，而不是
# 记在这个脚本里，因为升级时跑的是一个版本的脚本、面对的是另一个版本的代码，只有代码
# 自己知道它是用哪个验证的。
compose_browser_pin() {
  # 单引号：${...} 是要匹配的 compose 文本，不是展开。
  # shellcheck disable=SC2016
  sed -n 's/^ *CLOAKBROWSER_COMMIT: \${CLOAKBROWSER_COMMIT:-\([0-9a-f]\{40\}\)}.*$/\1/p' \
    "$INSTALL_DIR/docker/compose.yml" 2>/dev/null | head -n 1
}

# 有那个默认值之前的版本 —— 5.1.6 及更早 —— 在 .env 里没有 commit 时构建不出浏览器，
# 它的 .env.example 写着它要的那一个。
example_browser_pin() {
  sed -n 's/^CLOAKBROWSER_COMMIT=\([0-9a-f]\{40\}\)$/\1/p' "$INSTALL_DIR/.env.example" 2>/dev/null | head -n 1
}

is_written_pin() {
  case " $CLOAKBROWSER_WRITTEN_PINS " in *" $1 "*) return 0 ;; esac
  return 1
}

# .env 里那个 CloakBrowser 版本如果是这个项目自己写进去的、而不是有人特意选的，
# 就拿掉，让构建用 docker/compose.yml 里的那个 —— 也就是代码里 requirements 验证过的
# 版本。一个九月装的实例就是这样留在了 0.5.10，升级它会构建出 0.5.10 配 Playwright
# 1.64，这个组合谁都没跑过。有人特意选的版本保留，归他自己，但会告诉他这不是验证过的
# 那个。不管开没开浏览器都做，这样之后再打开时构建出来的是对的。
settle_browser_pin() {
  local pin default legacy
  pin="$(env_value CLOAKBROWSER_COMMIT)"
  default="$(compose_browser_pin)"
  if [ -z "$default" ]; then
    legacy="$(example_browser_pin)"
    [ -n "$legacy" ] || return 0
    if [ -z "$pin" ] || { is_written_pin "$pin" && [ "$pin" != "$legacy" ]; }; then
      set_env_value CLOAKBROWSER_COMMIT "$legacy"
      ok ".env 里写上了 CLOAKBROWSER_COMMIT=${legacy:0:12}：这个版本要从那里写明的 commit 构建浏览器。"
    fi
    return 0
  fi
  [ -n "$pin" ] || return 0
  if is_written_pin "$pin"; then
    drop_env_value CLOAKBROWSER_COMMIT \
      "CLOAKBROWSER_COMMIT=${pin} 已被 install/${SCRIPT_NAME} 拿掉：不设置时跟随 docker/compose.yml。"
    ok "浏览器镜像现在跟随这份代码的 CloakBrowser 版本（${default:0:12}）。"
    if [ "$pin" != "$default" ]; then
      info "原来是 ${pin:0:12}，那是早先的版本写进 .env 的。"
    fi
  elif [ "$pin" != "$default" ]; then
    warn ".env 里的 CLOAKBROWSER_COMMIT 钉在 ${pin:0:12}；这是有人特意选的，所以保留。"
    info "这份代码的浏览器依赖是用 ${default:0:12} 验证的。"
    info "想跟随代码，就把 .env 里那一行删掉。"
  fi
}

# Compose 会把这个路径从 .env 读进简写的卷定义里：冒号会截断路径，空格或 '#'
# 会先一步改变这一行的含义。
valid_model_path() {
  valid_path "$1" || return 1
  case "$1" in
    *[!A-Za-z0-9._/~+-]*) warn "这个路径里只能用字母、数字和 . _ - + /。"; return 1 ;;
  esac
  local dir
  dir="$(absolute_path "$(expand_tilde "$1")")"
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then
    warn "${dir} 已存在但不是目录。"
    return 1
  fi
  # 模型放在它下面的 asr/、vad/ 和 punc/ 里，这个脚本只碰它的这一部分。一个装满别的
  # 东西的目录是某个人的目录 —— 家目录、/srv —— 而不是专门放模型的。
  if [ -d "$dir" ] && [ ! -d "$dir/asr" ] && [ ! -d "$dir/vad" ] && [ ! -d "$dir/punc" ] \
      && [ -n "$(ls -A "$dir" 2>/dev/null || true)" ]; then
    warn "${dir} 里已经有别的文件。请用一个新的或空的目录，比如 ${dir}/funasr-onnx。"
    return 1
  fi
  return 0
}

# 模型在哪里，或者应该放到哪里。已经有模型的目录原样使用；否则提议下载进去。
choose_model_path() {
  MODELS_TO_FETCH=0
  dim "模型留在这台机器上，以只读方式挂载。已经放好模型的目录会原样使用。"
  MODEL_PATH="$(ask_value "模型目录？" "${MODEL_PATH:-$INSTALL_DIR/models/funasr-onnx}" valid_model_path)"
  MODEL_PATH="$(absolute_path "$(expand_tilde "$MODEL_PATH")")"

  if models_present "$MODEL_PATH"; then
    ok "在 ${MODEL_PATH} 找到了模型。"
    if ! models_readable "$MODEL_PATH"; then
      warn "其中有些对其他用户不可读，而容器是以 UID 10001 读它们的。"
      if ask_yes_no "把它们设为可读吗（对它本身和其中的 asr、vad、punc 加 a+rX，不会变成可写）？" "y"; then
        open_models "$MODEL_PATH" || { warn "chmod 失败。转写保持关闭。"; WANT_TRANSCRIBER=0; }
      else
        WANT_TRANSCRIBER=0
        dim "在它们可读之前，转写保持关闭。"
      fi
    fi
    return 0
  fi

  dim "那里还没有一套完整的模型。"
  if ask_yes_no "现在下载吗（约 500 MB，每个文件都对 SHA-256）？" "y"; then
    MODELS_TO_FETCH=1
  else
    WANT_TRANSCRIBER=0
    dim "转写保持关闭。按 documents/zh/18-funasr-models.md 手动准备好模型，"
    dim "之后再从菜单里打开。"
  fi
}

# 在已经存在的 .env 里写 transcriber 那几行。已有的 token 保留：换个新密钥
# 没有任何好处，两边读到的都是文件里那一个。
write_transcriber_env() {
  local token
  token="$(env_value DTK_TRANSCRIBER_TOKEN)"
  [ -n "$token" ] || token="$(random_hex 24)"
  set_env_value DTK_TRANSCRIBER_URL "http://transcriber:9200"
  set_env_value DTK_TRANSCRIBER_TOKEN "$token"
  set_env_value DTK_TRANSCRIBER_MODEL_PATH "$MODEL_PATH"
  if [ "$USE_PUBLISHED" = 1 ] && [ -z "$(env_value DTK_TRANSCRIBER_IMAGE)" ]; then
    set_env_value DTK_TRANSCRIBER_IMAGE "$TRANSCRIBER_IMAGE"
  fi
}

# 一个装不上的可选组件不应让整个安装失败：没有它，其余部分照样值得要，
# 之后也能从菜单里打开。
transcription_off() {
  WANT_TRANSCRIBER=0
  set_env_value DTK_TRANSCRIBER_URL ""
  set_ctl_profile transcriber off
  warn "$1"
  info "转写已关闭，其余部分照常继续。之后可以从菜单里打开："
  info "bash install.zh.sh --manage，然后选「管理」里的 11。"
}

# ------------------------------------------------------------------ 步骤 --

# 每个问题的答案都能用环境变量预设 —— 这才让 --yes 成为一次完整的无人值守安装，
# 而不只是"全取默认值"。没设的项一律落到"替一个不在场的人做决定时最稳妥"的那个值。
INSTALL_DIR=""
BIND_HOST="${DTK_BIND_HOST:-127.0.0.1}"
BIND_PORT="${DTK_BIND_PORT:-8000}"
WANT_BROWSER="${DTK_ENABLE_BROWSER:-0}"
WANT_DOWNLOADER="${DTK_ENABLE_DOWNLOADER:-1}"
USE_PUBLISHED="${DTK_USE_PUBLISHED:-1}"
WANT_TRANSCRIBER="${DTK_ENABLE_TRANSCRIBER:-0}"
MODEL_PATH="${DTK_TRANSCRIBER_MODEL_PATH:-}"
MODELS_TO_FETCH=0
# 除非有人选了版本，否则为空：那样就用 docker/compose.yml 里的。
BROWSER_COMMIT="${CLOAKBROWSER_COMMIT:-}"
# 读一次，然后从环境里拿掉，连同这个脚本钉住的版本一起。Compose 插值时 shell 里的值
# 优先于 .env 里的，而这个脚本跑的每一个 dtkctl 都继承它的环境：shell 里有
# DTK_BIND_HOST=0.0.0.0 时，「保持在 127.0.0.1」这个回答写进了 .env，整套服务却照样
# 发布在了 0.0.0.0 上。
unset DTK_BIND_HOST DTK_BIND_PORT DTK_TRANSCRIBER_MODEL_PATH CLOAKBROWSER_COMMIT DTK_IMAGE_TAG

banner() {
  printf '\n'
  printf '%s  Douyin_TikTok_Download_API v5 · 引导式部署%s\n' "$C_BOLD" "$C_RESET"
  printf '%s  只支持 Docker。不问过你，不装任何东西、不改任何东西。%s\n' "$C_DIM" "$C_RESET"
  printf '\n'
}

report_host() {
  step "看一下这台机器"
  ok "$DISTRO_NAME ($ARCH)"
  ok "$CPUS 核 CPU，$(( MEM_MIB / 1024 )).$(( (MEM_MIB % 1024) * 10 / 1024 )) GiB 内存"

  case "$ARCH" in
    x86_64|arm64) : ;;
    *) die "官方镜像只发布 x86_64 和 arm64，而这台是 ${ARCH}。
    你仍然可以自己从源码构建：$DOCS_URL" ;;
  esac

  if [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 1900 ]; then
    warn "内存不到 2 GiB。核心几个容器静息大约 300 MiB，但 PostgreSQL 会很难受，"
    warn "浏览器容器更是别想了。"
  fi
}

choose_directory() {
  step "装到哪里"
  local default_dir
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    default_dir="$DTK_INSTALL_DIR"
  elif [ "$(id -u)" = "0" ]; then
    default_dir="/opt/dtk"
  else
    default_dir="$HOME/dtk"
  fi
  dim "代码、你的 .env 和控制脚本放在这里。媒体文件和数据库放在 Docker 数据卷里，"
  dim "不在这个目录下。"
  INSTALL_DIR="$(ask_value "安装目录？" "$default_dir" valid_path)"
  INSTALL_DIR="$(absolute_path "$(expand_tilde "$INSTALL_DIR")")"

  case "$INSTALL_DIR" in
    ""|"/"|"/usr"|"/etc"|"/var"|"/bin"|"/sbin"|"/lib"|"/boot"|"/home"|"/root")
      die "拒绝安装到 ${INSTALL_DIR}。" ;;
  esac
}

# 这次安装会落到的那个同名项目留下的数据卷。Compose 按项目名给数据卷命名，所以新的
# 安装会对着那个旧数据库启动 —— 用的是它不认识的新密码 —— 然后死在第一次迁移上。
# 以前真的会走到那一步：用 `down` 停掉的安装，或者目录已经被删掉的安装，都没有任何
# 容器可供查找。
refuse_foreign_volumes() {
  [ -f "$INSTALL_DIR/.env" ] && return 0
  local volumes
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
  [ -n "$volumes" ] || return 0
  die "Docker 里已经有名为 '${PROJECT}' 的项目的数据卷：
$(printf '%s\n' "$volumes" | sed 's/^/      /')
    它们属于一份被停掉或被删掉、却没有带走数据卷的安装，而它们的数据库只有那份安装的
    .env 才能打开，$INSTALL_DIR 里没有那份 .env。新安装会生成新密码，对着它们必然失败。
      - 那份安装还在某个地方：到那里用 --manage 跑这个脚本，
        或者把 DTK_INSTALL_DIR 设成它所在的目录。
      - 换个名字、换个目录和它并存安装：
          DTK_PROJECT=${PROJECT}2 DTK_INSTALL_DIR=\$HOME/${PROJECT}2 bash install.zh.sh
      - 从头来过。这会永久删除那些数据：
          docker volume rm $(printf '%s' "$volumes" | tr '\n' ' ')"
}

# 除了 GitHub，换到任何地方都要先得到同意，而且要在从它取任何东西之前——resolve_target
# 已经会去那里读发布版本的 tag。镜像经手全部代码——docker/compose.yml，也就决定了跑哪些
# 镜像——以及这之后的每一次升级，而这里没法分辨它给的和 GitHub 上的是不是同一份。所以
# 把话说全，直接回车什么都不改。--yes 时，设了那个变量就是回答，跟 DTK_ENABLE_BROWSER=1
# 一样：是有人特意设的。
confirm_mirror() {
  [ "$REPO_URL" = "$GITHUB_REPO_URL" ] && return 0
  local default="n"
  [ "$ASSUME_YES" = 1 ] && default="y"
  step "用镜像代替 GitHub"
  warn "这次会从 ${REPO_URL} 克隆，而不是 GitHub。"
  dim "装进来的一切都经它的手：代码、docker/compose.yml（也就决定了跑哪些镜像），"
  dim "以及之后的每一次升级。这里没法分辨它给的和 GitHub 上的是不是同一份，被攻破的、"
  dim "或者你不了解的人运营的镜像，可以换给你别的代码。只用你自己运营或信得过的镜像。"
  dim "以后要切回 GitHub："
  dim "  git -C $INSTALL_DIR remote set-url origin $GITHUB_REPO_URL"
  ask_yes_no "从这个镜像克隆吗？" "$default" \
    || die "什么都没改。不设 DTK_GH_PROXY 和 DTK_REPO_URL，就会从 GitHub 克隆。"
}

prepare_directory() {
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR 已存在，而且不是目录。"
  fi
  have git || die "没有装 git。用 $(git_install_hint) 装上，然后再跑一次。"
  # 只有克隆才用它：已有的代码目录从它自己的 origin 取，DTK_GH_PROXY 设成什么都不管用。
  [ -d "$INSTALL_DIR/.git" ] || confirm_mirror

  # 要安装的那个版本：检出它的 tag，并把它的版本号作为 DTK_IMAGE_TAG 写进 .env。main
  # 跑在发布版本前面，以前克隆 main 的安装，一开始就是 main 的 compose.yml 套着上一个
  # 发布版本的镜像。
  resolve_target "${DTK_VERSION:-latest}"

  if [ -d "$INSTALL_DIR/.git" ]; then
    local origin
    origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "")"
    case "$origin" in
      *Douyin_TikTok_Download_API*) : ;;
      *) die "$INSTALL_DIR 是别的项目的 git 仓库（${origin}）。
    请换一个空目录。" ;;
    esac
    [ -f "$INSTALL_DIR/docker/compose.yml" ] \
      || die "$INSTALL_DIR 是这个项目 v5 之前的代码（里面没有 docker/compose.yml）。
    v5 和它没有任何共同之处。请换一个空目录。"
    if [ -x "$INSTALL_DIR/dtkctl" ] && [ "$(project_of "$INSTALL_DIR/dtkctl")" != "$PROJECT" ]; then
      die "$INSTALL_DIR 是项目 '$(project_of "$INSTALL_DIR/dtkctl")' 的安装，不是 '${PROJECT}' 的。
    里面的 .env 和 dtkctl 都属于那份安装。请换一个目录：DTK_INSTALL_DIR=..."
    fi
    ok "在 $INSTALL_DIR 发现已有的代码目录。"
    info "你的 .env 和 Docker 数据卷不会被动。"
    if checkout_is_at "$RELEASE_REF"; then
      ok "它已经在 $RELEASE_REF 了。"
    elif ask_yes_no "把它切到要安装的版本 $RELEASE_REF 吗？" "y"; then
      checkout_ref "$RELEASE_REF"
      ok "已检出 ${RELEASE_REF}。"
    else
      # 不切的话，代码目录会拿 $RELEASE_REF 的镜像去跑另一棵树的 compose.yml —— 切过去正是
      # 为了防止这种错配。
      die "那样会在另一份代码上跑 $RELEASE_REF 的镜像。什么都没改。
    要装别的版本，就指定它：DTK_VERSION=5.1.6 bash install.zh.sh"
    fi
    return
  fi

  if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    die "$INSTALL_DIR 非空，而且不是已有的安装。请换一个目录。"
  fi

  step "获取 $RELEASE_REF"
  local parent
  parent="$(dirname "$INSTALL_DIR")"
  # 先以当前用户来建：一个他自己就能建的上级目录也用 sudo，会在他的家目录里留下
  # 属于 root 的目录。
  if [ ! -d "$parent" ] && ! mkdir -p "$parent" 2>/dev/null; then
    ask_yes_no "$parent 不存在，需要 root 才能创建。用 sudo 创建吗？" "y" \
      || die "请自己创建 ${parent}，或者换一个目录。"
    as_root mkdir -p "$parent"
  fi
  if [ ! -w "$parent" ]; then
    ask_yes_no "$parent 不可写。用 sudo 创建 ${INSTALL_DIR}，并让它归你所有吗？" "y" \
      || die "请换一个你能写入的目录。"
    as_root mkdir -p "$INSTALL_DIR"
    as_root chown "$(id -u):$(id -g)" "$INSTALL_DIR"
  fi

  # 先完整克隆 main，再把发布版本放在它上面。5.1.6 的安装脚本 —— 在检出更新的版本之前，
  # `./dtkctl upgrade` 跑的就是它 —— 在交接之前会把代码目录快进到 origin/main，而这需要
  # main 上从那个发布版本到最新提交之间的历史：只克隆 tag 就没有 origin/main，--depth 1
  # 克隆 main 又只有最新提交、没有通往它的那段历史。不管哪种，那一步都会失败，升级就在
  # 旧代码里接着跑了下去。main 的全部历史大约 14 MB。
  local said hint="检查一下网络；在中国大陆多半要先换源（DTK_GH_PROXY，见 --help）：$DOCS_URL"
  # 镜像本身连不上时，再叫人去设一个镜像帮不上忙。
  [ "$REPO_URL" = "$GITHUB_REPO_URL" ] \
    || hint="这是一个镜像：确认它还在运行、并且转发本仓库；或者不设 DTK_GH_PROXY 和 DTK_REPO_URL，直接从 GitHub 克隆。"
  if ! said="$(git clone --quiet --single-branch --branch main "$REPO_URL" "$INSTALL_DIR" 2>&1)"; then
    die "克隆 $REPO_URL 失败：
$(printf '%s\n' "$said" | sed 's/^/      /')
    $hint"
  fi
  checkout_ref "$RELEASE_REF"
  ok "已把 $RELEASE_REF 克隆到 $INSTALL_DIR"
}

ask_questions() {
  step "问几个问题"

  dim "发布在 127.0.0.1 意味着只有这台机器自己能访问控制台。要对外开放，"
  dim "请在前面挂一个带 TLS 的反向代理；直接用 0.0.0.0 等于把一个不加密的"
  dim "管理后台裸放到网络上。"
  local loopback_default="y"
  [ "$BIND_HOST" = "0.0.0.0" ] && loopback_default="n"
  if ask_yes_no "保持在 127.0.0.1（推荐）？" "$loopback_default"; then
    BIND_HOST="127.0.0.1"
  else
    BIND_HOST="0.0.0.0"
    warn "将发布在 0.0.0.0。在别人能路由到这台机器之前，务必先挂上 TLS。"
  fi
  BIND_PORT="$(ask_value "用哪个端口？" "$BIND_PORT" valid_port)"

  printf '\n'
  dim "浏览器容器会自己铸造游客身份，你就不用手动粘 Cookie 了。它也是最贵的"
  dim "那部分：大约 4 GiB 内存、6 GB 镜像，而且这个镜像不发布，要在本机构建。"
  local browser_default="y"
  [ "${DTK_ENABLE_BROWSER:-}" = "0" ] && browser_default="n"
  [ "${DTK_ENABLE_BROWSER:-}" = "1" ] && browser_default="y"
  # 无人值守时一律不建它。那是一次要好几分钟、6 GB 大的镜像构建，过程中要访问
  # GitHub 和浏览器 CDN —— 不该因为有人加了 --yes 就自动开始。
  if [ "$ASSUME_YES" = 1 ]; then
    browser_default="n"
  elif [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 6000 ]; then
    browser_default="n"
    dim "这台机器内存不到 6 GiB，所以建议选否。不开它也能用：在控制台里导入"
    dim "你自己的 Cookie，其余功能一样正常。"
  fi
  # 显式设了 DTK_ENABLE_BROWSER=1 就压过"无人值守不建浏览器"这条规则：
  # 那是有人明确要求，不是替他做的默认决定。
  [ "${DTK_ENABLE_BROWSER:-}" = "1" ] && browser_default="y"
  if ask_yes_no "启用浏览器容器吗？" "$browser_default"; then WANT_BROWSER=1; else WANT_BROWSER=0; fi
  # 浏览器没开也要检查：不管开没开它都会写进 .env，之后打开浏览器的人会在一次构建
  # 进行了好几分钟之后才被 Docker 拒绝。
  if [ -n "$BROWSER_COMMIT" ] && ! valid_commit "$BROWSER_COMMIT"; then
    die "CLOAKBROWSER_COMMIT 必须是完整的 40 位 commit，而不是 '${BROWSER_COMMIT}'。"
  fi
  if [ "$WANT_BROWSER" = 1 ]; then
    local verified
    verified="$(compose_browser_pin)"
    [ -n "$verified" ] || verified="$(example_browser_pin)"
    if [ -z "$BROWSER_COMMIT" ]; then
      dim "它会安装 commit ${verified:0:12} 的 CloakBrowser，也就是这个版本验证过的那个，"
      dim "以后升级时会跟着代码走。"
    else
      dim "它会安装 commit ${BROWSER_COMMIT:0:12} 的 CloakBrowser，来自 CLOAKBROWSER_COMMIT。"
      if [ "$BROWSER_COMMIT" != "$verified" ]; then
        warn "这个版本验证过的是 ${verified:0:12}。你选的会写进 .env，升级时不会动它。"
      fi
    fi
  fi

  printf '\n'
  dim "下载器把媒体存到本机的 Docker 数据卷里。它很小很省；不开的话，下载请求"
  dim "会返回 501 并说明原因。"
  local dl_default="y"; [ "$WANT_DOWNLOADER" = 0 ] && dl_default="n"
  if ask_yes_no "启用媒体下载吗？" "$dl_default"; then WANT_DOWNLOADER=1; else WANT_DOWNLOADER=0; fi

  printf '\n'
  dim "离线转写在这台机器上把已存的视频转成文字，用的是针对普通话训练的 FunASR"
  dim "模型。工作时约占 1 GiB 内存和两个核，另需 500 MB 模型，脚本可以替你下载并校验。"
  # 和浏览器一样，不明确要求就不开：它要从第三方下载东西，又是第二个吃 CPU 的
  # 容器，不该因为有人加了 --yes 就自动装上。
  local tx_default="n"
  [ "${DTK_ENABLE_TRANSCRIBER:-}" = "1" ] && tx_default="y"
  if [ "$WANT_DOWNLOADER" = 0 ]; then
    dim "它转写的是下载器存下来的视频，所以媒体下载关着时它也保持关闭。"
    WANT_TRANSCRIBER=0
  elif [ "$CPUS" -lt 2 ]; then
    dim "它至少要两个核，这台机器只有一个，所以保持关闭。"
    WANT_TRANSCRIBER=0
  else
    if ask_yes_no "启用离线转写吗？" "$tx_default"; then WANT_TRANSCRIBER=1; else WANT_TRANSCRIBER=0; fi
    if [ "$WANT_TRANSCRIBER" = 1 ] && [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 4000 ]; then
      warn "内存不到 4 GiB。装得下，但请给这台机器配上 swap。"
    fi
  fi
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    choose_model_path
  fi

  printf '\n'
  dim "用已发布的镜像，拉下来一分钟左右就能起。从源码构建要好几分钟，而且需要"
  dim "npm 和 uv 都能连上网。"
  local pub_default="y"; [ "$USE_PUBLISHED" = 0 ] && pub_default="n"
  if ask_yes_no "用已发布的镜像（推荐）？" "$pub_default"; then USE_PUBLISHED=1; else USE_PUBLISHED=0; fi

}

# 放在写 .env 和 dtkctl 之前：下载失败时，得到的是一份关着转写的安装，而不是
# 一份指向不存在的模型的安装。
fetch_models() {
  if [ "$WANT_TRANSCRIBER" = 0 ] || [ "$MODELS_TO_FETCH" = 0 ]; then
    return 0
  fi
  if ! download_models "$MODEL_PATH"; then
    WANT_TRANSCRIBER=0
    warn "转写保持关闭。已经下好的部分会保留，下次只下载剩下的。"
  fi
}

write_env() {
  step "写 .env"
  local env_file="$INSTALL_DIR/.env"

  if [ -f "$env_file" ]; then
    ok "保留已有的 .env —— 现在的数据库就是用里面那些密钥建起来的。"
    info "想重新生成就自己删掉它，但那样旧的数据卷就打不开了。"
    reconcile_env
    return
  fi

  local pg_pass redis_pass secret dl_token tx_token
  pg_pass="$(random_hex 24)"
  redis_pass="$(random_hex 24)"
  secret="$(random_b64 48)"
  dl_token="$(random_hex 24)"
  tx_token="$(random_hex 24)"

  # 从文件诞生那一刻就是 0600：这个文件就是这个实例的钥匙。
  local old_umask
  old_umask="$(umask)"
  umask 077
  {
    printf '# 由 install/install.zh.sh 于 %s 生成。\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')"
    printf '# 里面的密钥就是加密你的数据库和 cookie jar 用的那些。\n'
    printf '# 请备份到安全的地方。丢了它 = 丢掉所有已存的凭据。\n\n'
    printf 'DTK_SECRET_KEY=%s\n' "$secret"
    printf 'POSTGRES_PASSWORD=%s\n' "$pg_pass"
    printf 'REDIS_PASSWORD=%s\n' "$redis_pass"
    printf 'DTK_DATABASE_URL=postgresql+asyncpg://dtk:%s@postgres:5432/dtk\n' "$pg_pass"
    printf 'DTK_REDIS_URL=redis://:%s@redis:6379/0\n\n' "$redis_pass"
    printf 'DTK_LOG_LEVEL=info\n'
    printf 'DTK_LOG_JSON=true\n\n'
    if [ "$WANT_BROWSER" = 1 ]; then
      printf 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000\n'
    else
      printf 'DTK_BROWSER_RPC_URL=\n'
    fi
    if [ "$WANT_DOWNLOADER" = 1 ]; then
      printf 'DTK_DOWNLOADER_URL=http://downloader:9100\n'
      printf 'DTK_DOWNLOADER_TOKEN=%s\n' "$dl_token"
    else
      printf 'DTK_DOWNLOADER_URL=\n'
      printf 'DTK_DOWNLOADER_TOKEN=\n'
    fi
    if [ "$WANT_TRANSCRIBER" = 1 ]; then
      printf 'DTK_TRANSCRIBER_URL=http://transcriber:9200\n'
      printf 'DTK_TRANSCRIBER_TOKEN=%s\n' "$tx_token"
      printf 'DTK_TRANSCRIBER_MODEL_PATH=%s\n' "$MODEL_PATH"
    else
      printf 'DTK_TRANSCRIBER_URL=\n'
      printf 'DTK_TRANSCRIBER_TOKEN=\n'
      printf 'DTK_TRANSCRIBER_MODEL_PATH=\n'
    fi
    printf '\n'
    printf 'DTK_BIND_HOST=%s\n' "$BIND_HOST"
    printf 'DTK_BIND_PORT=%s\n' "$BIND_PORT"
    # 只写有人选过的版本。不写的话用 docker/compose.yml 的默认值，它会随升级移动；
    # 写在这里的话，代码里浏览器的 requirements 往前走了，它还留在原地。
    # 有那个默认值之前的版本，由下面的 settle_browser_pin 给它写上 commit。
    if [ -n "$BROWSER_COMMIT" ]; then
      printf 'CLOAKBROWSER_COMMIT=%s\n' "$BROWSER_COMMIT"
    elif [ -n "$(compose_browser_pin)" ]; then
      printf '# CLOAKBROWSER_COMMIT= 不设置：浏览器跟随 docker/compose.yml。\n'
    fi
    if [ "$USE_PUBLISHED" = 1 ]; then
      printf 'DTK_IMAGE=evil0ctal/douyin_tiktok_download_api\n'
      # 写检出的那个发布版本，而不是 `latest`：那个 tag 会移动，而一份安装应该说得出
      # 自己跑的是哪个构建 —— 升级时钉住它要升到的版本，也是同一个理由。
      printf 'DTK_IMAGE_TAG=%s\n' "$RELEASE_TAG"
      printf 'DTK_DOWNLOADER_IMAGE=evil0ctal/douyin_tiktok_download_api-downloader\n'
      printf 'DTK_TRANSCRIBER_IMAGE=%s\n' "$TRANSCRIBER_IMAGE"
    fi
  } >"$env_file"
  umask "$old_umask"
  chmod 600 "$env_file"
  # compose.yml 有默认值之前的版本，要的是写在这里的 commit。
  settle_browser_pin
  ok "已写入 ${env_file}（仅属主可读）。"
}

# 一份中途停下的运行留下的 .env，为了它的密钥会保留下来，但刚刚给出的回答会覆盖掉
# 它们所决定的那几行。以前它保留自己的值，而 dtkctl 用的是新的回答：浏览器开了却没有
# 它的地址，下载开了却没有下载器的 URL，摘要里写着 127.0.0.1，而 .env 发布在 0.0.0.0 上。
reconcile_env() {
  set_env_value DTK_BIND_HOST "$BIND_HOST"
  set_env_value DTK_BIND_PORT "$BIND_PORT"
  if [ "$WANT_BROWSER" = 1 ]; then
    set_env_value DTK_BROWSER_RPC_URL "http://browser-rpc:9000"
  else
    set_env_value DTK_BROWSER_RPC_URL ""
  fi
  if [ "$WANT_DOWNLOADER" = 1 ]; then
    set_env_value DTK_DOWNLOADER_URL "http://downloader:9100"
    [ -n "$(env_value DTK_DOWNLOADER_TOKEN)" ] || set_env_value DTK_DOWNLOADER_TOKEN "$(random_hex 24)"
  else
    set_env_value DTK_DOWNLOADER_URL ""
  fi
  # dtkctl 马上要启动 transcriber，而一份在它出现之前写的 .env 会让 api
  # 不知道它的地址，它自己也没有 token。
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    write_transcriber_env
  else
    set_env_value DTK_TRANSCRIBER_URL ""
  fi
  if [ "$USE_PUBLISHED" = 1 ]; then
    set_env_value DTK_IMAGE evil0ctal/douyin_tiktok_download_api
    set_env_value DTK_IMAGE_TAG "$RELEASE_TAG"
    set_env_value DTK_DOWNLOADER_IMAGE evil0ctal/douyin_tiktok_download_api-downloader
  else
    set_env_value DTK_IMAGE ""
    set_env_value DTK_DOWNLOADER_IMAGE ""
    set_env_value DTK_TRANSCRIBER_IMAGE ""
  fi
  [ -z "$BROWSER_COMMIT" ] || set_env_value CLOAKBROWSER_COMMIT "$BROWSER_COMMIT"
  settle_browser_pin
}

# 容器要的核数超过宿主机实际核数时，Docker 会直接拒绝启动 —— 是硬报错，不是变慢；
# 而在小机器上，仓库自带的内存上限加起来比实际内存还多。所以比"默认值所针对的机器"
# 更小的宿主机，会拿到一份属于它自己的覆盖文件。
write_host_override() {
  local override="$INSTALL_DIR/compose.host.yml"
  local need_cpu=0 need_mem=0

  [ "$CPUS" -lt 4 ] && need_cpu=1
  [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 7800 ] && need_mem=1

  local previous=""
  if [ -f "$override" ]; then
    previous="$(mktemp)" || die "无法创建临时文件。"
    TMP_FILES+=("$previous")
    cat "$override" >"$previous"
  fi

  if [ "$need_cpu" = 0 ] && [ "$need_mem" = 0 ]; then
    rm -f "$override"
    keep_edited_override "$previous" "$override"
    return
  fi

  step "按这台机器调整资源上限"
  # compose.yml 里上限高于这台宿主机的每一个服务都要管，而且永远不超过宿主机：
  # 容器要的核数超过实际核数时 Docker 会拒绝。以前在两核及以下的机器上一律给 API 固定
  # 1.5 —— 而 migrate 和 worker 还留在 compose 给的 2.0 —— 结果单核机器连第一次迁移都
  # 跑不了。
  # api_cpu 管 api 和 worker，full_cpu 管 compose 给 2.0 的那几个（migrate、
  # transcriber）。
  local api_cpu full_cpu browser_cpu
  case "$CPUS" in
    1) api_cpu="1.0"; full_cpu="1.0"; browser_cpu="0.5" ;;
    # 至少留半个核：浏览器把每个核都占死的话，铸造期间还要响应请求的 API 就饿死了。
    2) api_cpu="1.5"; full_cpu="2.0"; browser_cpu="1.5" ;;
    3) api_cpu="2.0"; full_cpu="2.0"; browser_cpu="3.0" ;;
    *) api_cpu="2.0"; full_cpu="2.0"; browser_cpu="4.0" ;;
  esac

  local redis_mib=$(( MEM_MIB / 12 )) app_mib=$(( MEM_MIB / 9 )) dl_mib=$(( MEM_MIB / 20 ))
  {
    printf '# 由 install/install.zh.sh 按这台机器生成：%s 核 CPU，%s MiB 内存。\n' "$CPUS" "$MEM_MIB"
    printf '# docker/compose.yml 是按 4 vCPU / 8 GiB 的机器写的。那些上限不是预留，\n'
    printf '# 但容器要的核数超过实际核数时 Docker 会直接拒绝启动；而在小机器上，\n'
    printf '# 内存上限加起来会超过实际内存 —— 于是一次不走运的铸造会让 OOM killer\n'
    printf '# 杀掉 PostgreSQL，而不是那个真正在涨的进程。\n'
    printf '#\n'
    printf '# 这些值可以随便改。删掉这个文件就回到默认值。\n'
    printf 'services:\n'
    if [ "$need_mem" = 1 ]; then
      # compose.yml 里带 memswap_limit 的服务，每个 mem_limit 都要配上它。memswap_limit
      # 低于 mem_limit 的容器 Docker 会拒绝，而 api 和 worker 继承的 512m，在大约 4.6
      # 到 7.6 GiB 之间的每一台机器上都低于 MEM/9。
      printf '  postgres:\n    mem_limit: %sm\n' "$(( MEM_MIB / 4 ))"
      printf '  redis:\n    mem_limit: %sm\n' "$redis_mib"
      printf '  migrate:\n    cpus: %s\n' "$full_cpu"
      printf '  api:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: %s\n' "$app_mib" "$app_mib" "$api_cpu"
      printf '  worker:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: %s\n' "$app_mib" "$app_mib" "$api_cpu"
      printf '  browser-rpc:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 3 ))" "$browser_cpu"
      printf '  downloader:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: 1.0\n' "$dl_mib" "$dl_mib"
      # 固定值而不是按内存比例：它需要的就是模型加一个解码后的文件那么多（3 分钟
      # 的片段实测 0.9 GiB；一小时音频再多约 230 MB），和宿主机大小无关。和 compose
      # 一样不给 swap，因为换页的推理比失败的推理更慢。
      printf '  transcriber:\n    mem_limit: 1536m\n    memswap_limit: 1536m\n    cpus: %s\n' "$full_cpu"
    else
      printf '  migrate:\n    cpus: %s\n' "$full_cpu"
      printf '  api:\n    cpus: %s\n' "$api_cpu"
      printf '  worker:\n    cpus: %s\n' "$api_cpu"
      printf '  browser-rpc:\n    cpus: %s\n' "$browser_cpu"
      printf '  downloader:\n    cpus: 1.0\n'
      printf '  transcriber:\n    cpus: %s\n' "$full_cpu"
    fi
  } >"$override"
  ok "已写入 compose.host.yml —— 上限已按 $CPUS 核 / $MEM_MIB MiB 缩放。"
  keep_edited_override "$previous" "$override"

  # Redis 到 maxmemory 时淘汰数据，到 mem_limit 时被杀掉，所以前者必须比后者低不少。
  # compose.yml 的 320mb 在它的 512m 之下是这样；可在按小机器缩放过的上限之下就不是了，
  # Redis 本该淘汰数据，却被 OOM 杀掉了 —— 连同任务队列一起。有人设过的值保留。
  if [ "$need_mem" = 1 ] && [ $(( redis_mib * 6 / 10 )) -lt 320 ] && [ -z "$(env_value DTK_REDIS_MAXMEMORY)" ]; then
    set_env_value DTK_REDIS_MAXMEMORY "$(( redis_mib * 6 / 10 ))mb"
    ok "Redis 在 $(( redis_mib * 6 / 10 ))mb 时开始淘汰，低于它 ${redis_mib}m 的上限（.env 里的 DTK_REDIS_MAXMEMORY）。"
  fi
}

# 这个文件上写着"这些值可以随便改"，所以重写它的那次运行，在原来的内容不同时会把它
# 留下来，而不是把别人的改动扔掉。
keep_edited_override() {
  local previous="$1" override="$2"
  [ -n "$previous" ] && [ -s "$previous" ] || return 0
  if [ -f "$override" ] && cmp -s "$previous" "$override"; then return 0; fi
  cat "$previous" >"$override.old"
  info "原来的 compose.host.yml 保留为 compose.host.yml.old；有改动的话请从里面搬过来。"
}

# 之后所有命令的唯一入口。存在的理由是有三个参数每次都得带对，漏一个不会报错、
# 只会出怪事：项目名、两个 compose 文件，以及 COMPOSE_ENV_FILES —— 没有它，
# 插值会去读 docker/.env 而不是仓库根目录的 .env。
write_control_script() {
  local ctl="$INSTALL_DIR/dtkctl"
  local files="-f docker/compose.yml"
  [ -f "$INSTALL_DIR/compose.host.yml" ] && files="$files -f compose.host.yml"

  {
    printf '#!/usr/bin/env bash\n'
    printf '# 这套部署的唯一入口。由 install/install.zh.sh 生成。\n'
    printf '#\n'
    printf '#   ./dtkctl ps              看什么在跑\n'
    printf '#   ./dtkctl logs -f api     跟踪 API 日志\n'
    printf '#   ./dtkctl restart api     重启单个服务\n'
    printf '#   ./dtkctl down            停掉，保留数据\n'
    printf '#   ./dtkctl down -v         停掉并删除数据卷。不可恢复。\n'
    printf '#   ./dtkctl upgrade         升到最新的 Release：备份、拉取、迁移、重启\n'
    printf '#   ./dtkctl upgrade 5.1.6   改为升到指定的版本（加 --no-backup 跳过备份）\n'
    printf '#\n'
    printf '# 它替你带上的三样最容易忘的东西：项目名、本机资源覆盖文件，以及\n'
    printf '# COMPOSE_ENV_FILES —— 没有它，compose 会去找 docker/.env，然后\n'
    printf '# 悄悄用默认值做插值。\n'
    printf '#\n'
    printf '# 用已发布镜像时，版本号是 .env 里的 DTK_IMAGE_TAG，由 upgrade 来写。\n'
    printf 'set -euo pipefail\n'
    # 单引号是故意的：这些要在 dtkctl 运行时展开，不是现在。这个循环顺着指向 dtkctl
    # 的符号链接 —— 比如放进 PATH 的那种 —— 找回安装目录；只用 "$0" 的话，
    # docker/compose.yml 和安装脚本都找不到。
    # shellcheck disable=SC2016
    printf 'here="$0"\nwhile [ -L "$here" ]; do\n  link="$(readlink "$here")"\n'
    # shellcheck disable=SC2016
    printf '  case "$link" in /*) here="$link" ;; *) here="$(dirname "$here")/$link" ;; esac\ndone\n'
    # shellcheck disable=SC2016
    printf 'cd "$(dirname "$here")"\n'
    # 它不是 compose 的命令，所以不会挡住任何东西。以前升级要手动改 .env 里的
    # DTK_IMAGE_TAG，再自己记着按顺序更新代码、拉镜像、构建浏览器、跑迁移。
    # 随代码来的安装脚本知道这些步骤，也由它来写版本号。
    # shellcheck disable=SC2016
    printf 'if [ "${1:-}" = upgrade ]; then\n  shift\n'
    # 5.1.6 之前的版本，安装脚本没有 --upgrade。在这里就说清楚、并告诉他该改跑什么，
    # 而不是落到一句"未知选项"。
    printf '  if ! grep -q -- "--upgrade)" install/%s; then\n' "$SCRIPT_NAME"
    printf '    printf "%%s\\n" "这个版本自己没有升级命令。请运行当前的安装脚本：" "  curl -fsSL %s/install/%s -o install.zh.sh && bash install.zh.sh --upgrade" >&2\n' "$REPO_RAW" "$SCRIPT_NAME"
    printf '    exit 1\n  fi\n'
    # shellcheck disable=SC2016
    printf '  DTK_PROJECT=%s DTK_INSTALL_DIR="$PWD" exec bash install/%s --upgrade "$@"\nfi\n' "$PROJECT" "$SCRIPT_NAME"
    printf 'export COMPOSE_ENV_FILES=.env\n'
    printf 'exec docker compose -p %s %s' "$PROJECT" "$files"
    [ "$WANT_BROWSER" = 1 ] && printf ' --profile browser'
    [ "$WANT_DOWNLOADER" = 1 ] && printf ' --profile downloader'
    [ "$WANT_TRANSCRIBER" = 1 ] && printf ' --profile transcriber'
    printf ' "$@"\n'
  } >"$ctl"
  chmod 755 "$ctl"
  ok "已写入 dtkctl —— 之后用它，别直接敲 docker compose。"
}

# 在拉取任何东西之前，让 Compose 自己读一遍刚写好的配置。它不接受的项目名、端口或
# 文件，以前要到 `pull` 时才露出来，还顶着一条怪罪镜像仓库的消息。
validate_compose() {
  local problem
  if ! problem="$("$CTL" config -q 2>&1)"; then
    die "Compose 不接受这份安装的配置：
$(printf '%s\n' "$problem" | sed 's/^/      /')"
  fi
}

# 一次安装里第一条 compose 命令会创建它的网络，Docker 从固定的几个地址池里给每个
# 网络分一段子网。跑过很多套栈的机器会分光 —— 早就不在的项目，网络还占着地址 ——
# 而 compose 只报一行"address pools"，不告诉你该怎么办。
run_creating_networks() {
  local log status=0
  log="$(mktemp)" || die "创建临时文件失败。"
  TMP_FILES+=("$log")
  "$@" 2>&1 | tee "$log" || status=$?
  if [ "$status" != 0 ] && grep -qiE 'address pools have been fully subnetted|non-overlapping IPv4 address pool' "$log"; then
    warn "Docker 已经没有空闲的地址段给这份安装的网络用了。"
    info "每个 Docker 网络都要从一组固定的地址段里占一段，已经删掉的栈留下的网络"
    info "也照样占着。先删掉没有容器在用的网络："
    dim  "  docker network prune"
    info "如果腾不出来，就在 /etc/docker/daemon.json 里用 default-address-pools"
    info "给 Docker 多配几段地址，重启 Docker，再运行一次这个脚本。"
  fi
  return "$status"
}

bring_up() {
  step "启动服务"
  local ctl="$INSTALL_DIR/dtkctl"

  if [ "$USE_PUBLISHED" = 1 ]; then
    info "正在拉取镜像。第一次要几分钟。"
    # 只拉这次安装真正存在的服务：没启用 downloader profile 的部署上执行
    # `pull downloader` 是报错，不是空操作。
    local services=(api worker)
    [ "$WANT_DOWNLOADER" = 1 ] && services+=(downloader)
    # 故意不写成 `a || b | tail || die`：管道取的是最后一条命令的退出码，
    # `tail` 成功就会把失败的 pull 吞掉，安装会带着一堆没拉下来的镜像继续走。
    if ! "$ctl" pull "${services[@]}"; then
      die "拉取镜像失败。
    在中国大陆，这通常是镜像仓库的问题而不是项目的问题 —— 多半要在
    /etc/docker/daemon.json 里配一个加速器。$DOCS_URL"
    fi
  fi

  if [ "$WANT_BROWSER" = 1 ]; then
    info "正在构建浏览器镜像。这个镜像不发布，预计要好几分钟。"
    "$ctl" build browser-rpc || die "浏览器镜像构建失败。可以不开它再跑一次，或参见 $DOCS_URL"
  fi

  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    if [ "$USE_PUBLISHED" = 1 ]; then
      info "正在拉取转写镜像。"
      "$ctl" pull transcriber || transcription_off "转写镜像拉取失败。
    可能是当前用的版本还没有它，也可能是镜像仓库没有响应。"
    else
      info "正在构建转写镜像。"
      "$ctl" build transcriber || transcription_off "转写镜像构建失败。"
    fi
  fi

  info "正在执行数据库迁移。"
  run_creating_networks "$ctl" run --rm migrate || die "迁移失败，请看上面的输出。"

  info "正在启动各个服务。"
  run_creating_networks "$ctl" up -d --wait || die "服务没有全部进入健康状态。看一下：$ctl logs"
  ok "所有服务健康。"
}

show_result() {
  local ctl="$INSTALL_DIR/dtkctl" url=""
  step "完成"

  # API 首次启动会打印一次性的初始化地址。从日志里抠出来，别让人自己翻。
  url="$("$ctl" logs api 2>/dev/null | grep -oE 'http://[^ ]+/setup\?token=[A-Za-z0-9_-]+' | tail -1 || true)"

  local shown_host="$BIND_HOST"
  [ "$shown_host" = "0.0.0.0" ] && shown_host="127.0.0.1"

  printf '\n'
  if [ -n "$url" ]; then
    # API 打印的是它在容器*内部*绑定的地址，永远是 8000；而人要输的是对外发布的
    # 那个端口。所以重写主机和端口、保留 token，别给出一个没人监听的链接。
    url="${url#http://*/}"
    url="http://${shown_host}:${BIND_PORT}/${url}"
    printf '    %s打开这个地址创建第一个管理员：%s\n' "$C_BOLD" "$C_RESET"
    printf '    %s%s%s\n' "$C_GREEN" "$url" "$C_RESET"
    dim "这个链接 24 小时内有效，账号建好之后立即失效。"
  else
    printf '    %s控制台：%s http://%s:%s\n' "$C_BOLD" "$C_RESET" "$shown_host" "$BIND_PORT"
    dim "一次性的初始化链接在日志里：$ctl logs api"
  fi

  printf '\n'
  info "目录     $INSTALL_DIR"
  info "控制     $ctl ps | logs -f api | restart api | down"
  info "密钥     $INSTALL_DIR/.env  —— 请备份，数据库要靠它解密"
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    info "模型     $MODEL_PATH"
  fi
  info "文档     $DOCS_URL"
  if [ "$WANT_BROWSER" = 0 ]; then
    printf '\n'
    dim "浏览器容器没开，所以身份池不会自己补。"
    dim "在控制台里导入你自己的 Cookie：身份池 → 导入。"
  fi
  if [ "$WANT_TRANSCRIBER" = 1 ] && [ "$WANT_BROWSER" = 1 ]; then
    printf '\n'
    dim "转写和浏览器容器共用 CPU：赶上正在铸造身份时，一次转写会慢上好几倍。"
    dim "刚启动、身份池还在补满的那几分钟最忙。"
  fi
  if [ "$BIND_HOST" = "0.0.0.0" ]; then
    printf '\n'
    warn "这个控制台发布在所有网络接口上，而且没有 TLS。"
    warn "在它能被别处访问到之前，请先在前面挂一个反向代理。"
  fi
  printf '\n'
}

# ------------------------------------------------------------------ 管理 --
#
# 下面这些都是针对「已经装好」的实例。一个脚本干两件事是刻意的：半年前用它部署的
# 人，不该为了改个口令去学 `docker compose`。
#
# 每个操作都委托给 `dtkctl` 或 api 容器里的 `dtk` 命令。这里不重新实现项目已经
# 有的东西，所以菜单项不会和它背后那条命令走散。

CTL=""   # 正在管理的那套安装的 dtkctl 路径

# 这个脚本所在的代码目录 —— 只在脚本是磁盘上的一个文件时才有。经 `curl | bash` 管道
# 运行时它没有目录，以前拿来顶替的 "." 会让它变成运行时所在目录的上一级。
own_checkout() {
  local src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 1
  (cd "$(dirname "$src")/.." 2>/dev/null && pwd)
}

# DIR 是不是一份可以不经指明就当作安装来操作 —— 也就是会去运行它的 dtkctl —— 的目录：
# 恰好待在某个目录里、里面有个叫 dtkctl 的文件，不足以成为执行它的理由。它得是这个项目的
# 代码目录、带着 compose 文件，dtkctl 的属主是运行这个脚本的人或 root，而且别人写不了。
# DTK_INSTALL_DIR，以及 dtkctl 替自己传进来的目录，照说的算。
is_install_dir() {
  local dir="$1" origin
  [ -n "$dir" ] && [ -d "$dir/.git" ] && [ -f "$dir/docker/compose.yml" ] && [ -x "$dir/dtkctl" ] || return 1
  origin="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
  case "$origin" in *Douyin_TikTok_Download_API*) : ;; *) return 1 ;; esac
  [ -n "$(find "$dir/dtkctl" -maxdepth 0 \( -user "$(id -u)" -o -user 0 \) ! -perm -020 ! -perm -002 2>/dev/null)" ]
}

# 已有部署在哪 —— 问 Docker，不靠猜。Compose 建的每个容器都带着它来自哪些配置文件
# 的标签，安装目录就是第一个文件的上上级。从标签里读，而不是让 python3 去解析
# `compose ls`：没有 python3 的机器对那个的回答是空，脚本随后就在已有的安装上又装了一遍。
find_existing_install() {
  local config dir
  config="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.project.config_files"}}' 2>/dev/null | head -n 1 || true)"
  config="${config%%,*}"
  [ -n "$config" ] || return 1
  dir="$(dirname "$(dirname "$config")")"
  is_install_dir "$dir" || return 1
  printf '%s' "$dir"
}

# 一份已有安装开了哪些东西，从它自己的 dtkctl 和 .env 里读。菜单以前用的是这次
# 运行的环境变量，而那对那份安装一无所知：升级时会去拉一个它从没开过的下载器，
# 却会漏掉它开着的 transcriber。
load_install_state() {
  WANT_BROWSER=0
  WANT_DOWNLOADER=0
  WANT_TRANSCRIBER=0
  USE_PUBLISHED=0
  if grep -qF -- "--profile browser" "$CTL"; then WANT_BROWSER=1; fi
  if grep -qF -- "--profile downloader" "$CTL"; then WANT_DOWNLOADER=1; fi
  if grep -qF -- "--profile transcriber" "$CTL"; then WANT_TRANSCRIBER=1; fi
  if [ -n "$(env_value DTK_IMAGE)" ]; then USE_PUBLISHED=1; fi
  local recorded
  recorded="$(env_value DTK_TRANSCRIBER_MODEL_PATH)"
  [ -z "$recorded" ] || MODEL_PATH="$recorded"
}

# 运行中的实例自己报的版本。只有这个算数：磁盘上的代码可能比真正在服务的那个
# 镜像更新。
running_version() {
  "$CTL" exec -T api dtk --version 2>/dev/null | tr -d '\r' | awk 'NF{print $NF}' | tail -1
}

# 一个 v5 的发布 tag，别的都不算：v5.1.6 算，V4.1.3 不算。v4 分支还在出版本，用的是
# 大写 V 的 tag，GitHub 可能会把其中一个称作最新版；升级要是拿了它，就会把一个 v4 的
# 镜像拉进 v5 的栈里。
is_release_tag() {
  printf '%s' "$1" | grep -Eq '^v([5-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+$'
}

# 发布版本从哪里来：有了代码目录之后就是它自己的 origin，这样镜像就一直是那个镜像。
origin_url() {
  # 只认它自己的 .git：在别的工作区里面的一个空目录上跑 `git -C`，回答的是那个工作区的 origin。
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null && return 0
  fi
  printf '%s' "$REPO_URL"
}

# 远端上以 $1 开头的最高的发布 tag（"5.1" 对应 v5.1.，任意版本对应 v）。它是
# latest_release 的后备，也是 `5.1` 找到 5.1.6 的办法。
highest_release_tag() {
  local prefix="${1:-v}" best="" tag
  for tag in $(git ls-remote --tags --refs "$(origin_url)" "refs/tags/${prefix}*" 2>/dev/null \
      | sed -n 's|^[0-9a-f]*[[:space:]]*refs/tags/||p'); do
    is_release_tag "$tag" || continue
    if [ -z "$best" ] || version_is_newer "$tag" "$best"; then best="$tag"; fi
  done
  printf '%s' "$best"
}

# 最新的 v5 发布版本，以 tag 的形式给出。GitHub 的 "latest" 是 v5 版本时就用它 ——
# 发布公告指向的就是它，而且要等镜像都传上去它才会移动 —— 否则，或者 API 没应答时，
# 用最高的那个 tag。不用 python3：没有它的机器（Alpine、CoreOS）在这里拿到的是空，
# 于是永远看不到升级。这份 JSON 只要一个字段，用 sed 读。
latest_release() {
  local tag
  tag="$(curl -fsSL --proto '=https' --tlsv1.2 -m 10 \
    "https://api.github.com/repos/Evil0ctal/Douyin_TikTok_Download_API/releases/latest" 2>/dev/null \
    | sed -n 's/^ *"tag_name": *"\([^"]*\)".*$/\1/p' | head -n 1 || true)"
  if is_release_tag "$tag"; then
    printf '%s' "$tag"
  else
    highest_release_tag v
  fi
}

# $1 比 $2 新时返回真。带预发布后缀的版本比同样数字但没后缀的旧，所以
# 5.1.0.dev0 排在 5.1.0 前面 —— 和控制台里 web/src/lib/version.ts 同一套规则。
# 用 awk 而不是 python3，理由见 latest_release。
version_is_newer() {
  [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
  awk -v a="$1" -v b="$2" '
    function parse(raw, out,    text, n, i, parts, rest) {
      text = raw
      sub(/^[ \t]+/, "", text); sub(/[ \t]+$/, "", text); sub(/^[vV]/, "", text)
      if (!match(text, /^[0-9]+(\.[0-9]+)*/)) { out["n"] = 1; out[1] = 0; out["s"] = ""; return }
      n = split(substr(text, 1, RLENGTH), parts, ".")
      for (i = 1; i <= n; i++) out[i] = parts[i] + 0
      out["n"] = n
      rest = substr(text, RLENGTH + 1)
      sub(/^[.\-_]/, "", rest)
      out["s"] = rest
    }
    BEGIN {
      parse(a, x); parse(b, y)
      w = (x["n"] > y["n"]) ? x["n"] : y["n"]
      for (i = 1; i <= w; i++) {
        p = (i <= x["n"]) ? x[i] : 0
        q = (i <= y["n"]) ? y[i] : 0
        if (p != q) exit (p > q) ? 0 : 1
      }
      if (x["s"] == y["s"]) exit 1
      if (x["s"] == "") exit 0
      if (y["s"] == "") exit 1
      exit (x["s"] > y["s"]) ? 0 : 1
    }'
}

# resolve_target 接受的几种写法，在输入时就检查：以前在菜单的"升到哪个版本？"里打错一个字，
# 会死在 resolve_target 里，整个会话跟着结束。
valid_target() {
  case "$1" in
    latest) return 0 ;;
    sha-*) valid_commit "${1#sha-}" && return 0 ;;
    *) printf '%s' "${1#v}" | grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?$' && return 0 ;;
  esac
  warn "写一个版本号（如 5.1.6）、一个系列（如 5.1）、latest，或 sha-<40 位 commit>。"
  return 1
}

# 一个目标指的是什么，以升级需要的两种形式给出：RELEASE_TAG 用于 DTK_IMAGE_TAG
# （5.1.6、sha-<40 位>），RELEASE_REF 用于代码目录（v5.1.6、那个 commit）。`latest` 和
# `5.1` 会解析成它们所指的那个发布版本，这样 .env 钉住的是一个版本，而不是一个会移动的 tag。
RELEASE_TAG=""
RELEASE_REF=""
resolve_target() {
  local target="${1:-latest}" version
  RELEASE_TAG=""
  RELEASE_REF=""
  case "$target" in
    latest)
      RELEASE_REF="$(latest_release)" ;;
    sha-*)
      valid_commit "${target#sha-}" \
        || die "'${target}' 不是一个构建。sha- tag 带的是完整的 40 位 commit。"
      RELEASE_TAG="$target"
      RELEASE_REF="${target#sha-}"
      return 0 ;;
    *)
      version="${target#v}"
      if printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        RELEASE_REF="v$version"
      elif printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+$'; then
        RELEASE_REF="$(highest_release_tag "v$version.")"
      else
        die "'${target}' 不是一个版本。请写成 5.1.6 这样的版本、5.1 这样的系列、latest，或者 sha-<40 位 commit>。"
      fi ;;
  esac
  [ -n "$RELEASE_REF" ] || die "找不到 '${target}' 对应的发布版本。检查一下网络，或者直接写明版本：5.1.6。"
  is_release_tag "$RELEASE_REF" || die "'${target}' 不是 v5 的发布版本。"
  RELEASE_TAG="${RELEASE_REF#v}"
}

# 代码目录必须就是构建那些镜像的那棵树。它以前不管装的是什么都跟着 origin/main 走，
# 于是一个发布版本的镜像跑在 main 的 compose.yml、浏览器代码和安装脚本之下 —— 这些
# 正是 docker-publish.yml 特意不放进 `latest` 的东西 —— 而退回一个更旧的版本根本不可能
# 成功。用已发布镜像的安装检出它那个版本的 tag（detached）。从源码构建的安装一开始也在
# 一个发布版本上，它的升级跟着 main 走，因为它构建的就是代码目录里的东西。所有失败都停在
# 这里，在别的任何东西改动之前。
checkout_ref() {
  local ref="$1" dir="$INSTALL_DIR" origin
  origin="$(origin_url)"
  case "$ref" in
    main)
      git -C "$dir" fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" \
        || die "无法从 $origin 拉取 main。什么都没改。"
      if [ "$(git -C "$dir" symbolic-ref -q --short HEAD 2>/dev/null || true)" != main ]; then
        if git -C "$dir" show-ref --verify --quiet refs/heads/main; then
          git -C "$dir" checkout --quiet main 2>/dev/null || checkout_failed main
        else
          git -C "$dir" checkout --quiet -b main origin/main 2>/dev/null || checkout_failed main
        fi
      fi
      git -C "$dir" merge --ff-only --quiet origin/main 2>/dev/null \
        || die "$dir 里的 main 有不在 $origin 上的提交，所以没法直接往前快进。
    什么都没改。看一下：git -C $dir log --oneline origin/main..main" ;;
    # 这些都不加 --depth：在完整克隆里加上它会让仓库变成浅克隆，切断上面那次快进
    # 所依赖的历史。
    v*)
      git -C "$dir" fetch --quiet origin "+refs/tags/$ref:refs/tags/$ref" \
        || die "无法从 $origin 拉取 ${ref}。什么都没改。"
      git -C "$dir" checkout --quiet --detach "refs/tags/$ref" 2>/dev/null || checkout_failed "$ref" ;;
    *)
      git -C "$dir" fetch --quiet origin "$ref" \
        || die "无法从 $origin 拉取 commit ${ref}。什么都没改。"
      git -C "$dir" checkout --quiet --detach "$ref" 2>/dev/null || checkout_failed "$ref" ;;
  esac
  [ -f "$dir/docker/compose.yml" ] || die "$ref 里没有 docker/compose.yml，它不是一棵 v5 的代码树。"
}

checkout_failed() {
  die "$INSTALL_DIR 里的代码有本地改动，切到 $1 会把它们覆盖掉：
$(git -C "$INSTALL_DIR" status --short --untracked-files=no 2>/dev/null | sed 's/^/      /')
    什么都没改。把它们挪进一个覆盖文件 —— compose.host.yml 就是一个 ——
    或者先用这条命令收起来：git -C $INSTALL_DIR stash"
}

# 代码目录是不是已经就是 $1 所指的东西：一个 tag 或一个 commit。
checkout_is_at() {
  local head want
  head="$(git -C "$INSTALL_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)"
  case "$1" in
    v*) want="$(git -C "$INSTALL_DIR" rev-parse -q --verify "refs/tags/$1^{commit}" 2>/dev/null || true)" ;;
    main) return 1 ;;
    *) want="$1" ;;
  esac
  [ -n "$head" ] && [ "$head" = "$want" ]
}

ask_choice() {
  local prompt="$1" reply
  printf '\n    %s ' "$prompt" >&2
  reply="$(read_line)"
  printf '%s' "$(printf '%s' "$reply" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
}

# 不回显地读两遍口令。调用方把它从标准输入喂给命令；绝不作为参数传 ——
# argv 对同机任何进程都可见。什么都不输，或者终端没了，就是取消（返回 1）：它以前
# 会回答「太短了」然后再问一遍，永远问下去 —— 终端关掉时，循环能转多快就问多快。
# 调用方会设上 SECRET_PROMPTED，这样在这里按 Ctrl-C 不会让终端停在关掉回显的状态。
read_password_twice() {
  local first second
  [ -n "$TTY_IN" ] || return 1
  while true; do
    printf '    新口令（至少 8 位，留空取消）：' >&2
    IFS= read -rs first <"$TTY_IN" || { printf '\n' >&2; return 1; }
    printf '\n' >&2
    [ -n "$first" ] || return 1
    if [ "${#first}" -lt 8 ]; then
      warn "太短了。"
      continue
    fi
    printf '    再输一遍：' >&2
    IFS= read -rs second <"$TTY_IN" || { printf '\n' >&2; return 1; }
    printf '\n' >&2
    if [ "$first" != "$second" ]; then
      warn "两次不一致。"
      continue
    fi
    printf '%s' "$first"
    return 0
  done
}

# 破坏性操作要求打出一个词。和前面五个 y/n 一样节奏地再敲一个 y，那不叫决定。
confirm_word() {
  local word="$1" reply
  printf '    打出 %s 确认，输别的即取消：' "$word" >&2
  reply="$(read_line)"
  [ "$reply" = "$word" ]
}

pause_for_reader() {
  [ -n "$TTY_IN" ] || return 0
  printf '\n    回车返回。' >&2
  read_line >/dev/null
}

# ------------------------------------------------------------------ 操作 --

show_status() {
  step "状态"
  local installed latest
  installed="$(running_version || true)"
  if [ -n "$installed" ]; then
    ok "运行中的版本 $installed"
  else
    warn "没有东西在跑，或者 api 容器没应答。"
  fi
  printf '\n'
  "$CTL" ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true

  printf '\n'
  info "Docker 占用的磁盘："
  docker system df 2>/dev/null | sed 's/^/      /'

  # 只有确实知道时才说「已经是最新」。以前只要读得到最新版就这么回答 —— API 没起来
  # 的时候，或者实例跑在发布版本前面的时候，也是这样。
  latest="$(latest_release)"
  [ -n "$latest" ] || return 0
  printf '\n'
  if [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    warn "已经有 $latest 了，这个实例还在 ${installed}。"
  elif [ "$installed" = "${latest#v}" ]; then
    ok "已经是最新发布版（${latest}）。"
  else
    info "最新的发布版是 ${latest}。"
  fi
}

# 带着要升到的版本号去跑，这样升级途中的拉取和构建要的是新镜像，而 .env 里写的
# 还是旧的。源码构建的安装没有版本号可写：它沿用 .env 给它的 tag，要是设成空值，
# 构建出来的就会盖掉 `dev` 镜像。
with_target_tag() {
  if [ -n "$UPGRADE_TO" ]; then
    DTK_IMAGE_TAG="$UPGRADE_TO" "$@"
  else
    "$@"
  fi
}

# 一次升级要升到什么：UPGRADE_TO 用于 DTK_IMAGE_TAG（5.1.7、sha-<40 位>；源码安装为空），
# UPGRADE_REF 用于代码目录（v5.1.7、那个 commit，或者 main）。
UPGRADE_TO=""
UPGRADE_REF=""
# 代码目录原来在哪里，以及 .env 原来的一份副本，给 undo_upgrade 用。
UNDO_SHA=""
UNDO_BRANCH=""
UNDO_ENV=""

# 从有人要求的东西 —— 一个版本、一个系列、latest、一个 sha- 构建，或者什么都没写，
# 那就是最新的发布版本 —— 得出这次升级要升到哪里。从源码构建的安装则跟着 main 走。
plan_upgrade() {
  if [ "$USE_PUBLISHED" = 0 ]; then
    if [ -n "${1:-}" ]; then
      die "这份安装的镜像是从源码构建的，跑的就是代码目录里的那份，
    没有已发布的版本可选。直接运行 ./dtkctl upgrade 即可。"
    fi
    UPGRADE_TO=""
    UPGRADE_REF="main"
    return 0
  fi
  resolve_target "${1:-latest}"
  UPGRADE_TO="$RELEASE_TAG"
  UPGRADE_REF="$RELEASE_REF"
}

# 把代码目录和 .env 恢复成升级开始时的样子。UPGRADE_UNDO 设着的时候 —— 从挪动代码
# 目录开始，到整套服务切换完为止 —— cleanup 会跑它，所以一次在中间停下的升级，不管是
# 出错还是按了 Ctrl-C，都会让安装保持原样。以前说"什么都没改"的时候，代码目录已经挪到
# 了 main，.env 也已经钉在了新版本上。
undo_upgrade() {
  UPGRADE_UNDO=""
  if [ -n "$UNDO_ENV" ] && [ -f "$UNDO_ENV" ]; then
    cat "$UNDO_ENV" >"$INSTALL_DIR/.env"
    rm -f "$UNDO_ENV"
  fi
  if [ -n "$UNDO_SHA" ]; then
    local moved=0
    if [ -n "$UNDO_BRANCH" ]; then
      git -C "$INSTALL_DIR" checkout --quiet "$UNDO_BRANCH" 2>/dev/null \
        && git -C "$INSTALL_DIR" reset --quiet --keep "$UNDO_SHA" 2>/dev/null && moved=1
    else
      git -C "$INSTALL_DIR" checkout --quiet --detach "$UNDO_SHA" 2>/dev/null && moved=1
    fi
    if [ "$moved" = 0 ]; then
      warn "没能把代码目录放回原处。手动恢复："
      dim "git -C $INSTALL_DIR checkout ${UNDO_BRANCH:-$UNDO_SHA}"
      return 0
    fi
  fi
  local where
  where="$(git -C "$INSTALL_DIR" describe --tags --exact-match 2>/dev/null || true)"
  warn "已恢复：代码目录重新回到 ${UNDO_BRANCH:-${where:-${UNDO_SHA:0:12}}}，.env 也和原来一样。"
  info "原来在跑的版本还在跑。"
}

# 迁移只会往前走，所以迁移之前的那一刻最值得留一份归档。由现在正在跑的 api —— 也就是
# 旧版本 —— 写进备份卷。没有东西在跑，就没有谁来做这份备份，这时只说一声，而不是让
# 升级停下 —— 也许正是这次升级能让它重新跑起来。
backup_before_upgrade() {
  if [ "$NO_BACKUP" = 1 ]; then
    info "不先备份（--no-backup）。"
    return 0
  fi
  if [ -z "$(running_version || true)" ]; then
    warn "API 没在跑，所以这次升级之前没有备份。"
    return 0
  fi
  info "先备份，存进备份卷。"
  # 单引号：DTK_BACKUP_DIR 要在容器里展开，不是在这里。
  # shellcheck disable=SC2016
  if ! "$CTL" exec -T api sh -c 'dtk backup create -o "$DTK_BACKUP_DIR"'; then
    die "备份失败，所以什么都没有升级。看一下上面它的输出，或者不备份直接
    升级：./dtkctl upgrade ${UPGRADE_TO:+$UPGRADE_TO }--no-backup"
  fi
}

# 迁移不会往回走，所以一个不认识这个数据库当前所在迁移的版本，没法跑在它上面。在拉取
# 任何东西之前就先问：正在跑的 api 说出它迁移到的 head，刚检出的代码里要么有这个迁移，
# 要么没有。没有这一步的话，答案要到迁移那一步才出来，那时已经拉过镜像、构建过浏览器、
# 还做过一次备份了。
refuse_unknown_schema() {
  local running="$1" head
  head="$("$CTL" exec -T api dtk migrate --show 2>/dev/null | awk '/revision on disk/ {print $NF}' || true)"
  case "$head" in ''|none) return 0 ;; esac
  if ! ls "$INSTALL_DIR/src/dtk/db/migrations/versions/${head}_"*.py >/dev/null 2>&1; then
    die "「${UPGRADE_TO:-这份代码}」没法跑在这个数据库上：它在迁移 ${head}，
    而「${UPGRADE_TO:-这份代码}」里没有这个迁移${running:+ —— 是 ${running} 把它迁移到那里的}。迁移不会
    往回走。回去的办法是重新装一份${UPGRADE_TO:+ ${UPGRADE_TO}}、沿用这份 .env —— 备份只能用
    做它时的 DTK_SECRET_KEY 打开 —— 再把在它上面做的备份恢复进去。"
  fi
}

# 升到 UPGRADE_TO / UPGRADE_REF 的这次升级，顺序本身就是重点：先挪代码目录，并交给
# 随它而来的安装脚本；然后在 undo_upgrade 待命的情况下，做所有可能失败、又不碰正在跑的
# 服务的事 —— Compose 读一遍新代码、拉取、构建浏览器、备份、迁移；这些都过了，才在
# .env 里钉住版本、切换整套服务。迁移以前是在钉版本之后跑的，所以升到一个更旧的版本
# 会在那里失败，而 .env 留着的却是那个版本。
do_upgrade() {
  local before
  if [ -z "${DTK_UPGRADE_AT_TARGET:-}" ]; then
    step "升级${UPGRADE_TO:+到 $UPGRADE_TO}"
    info "你的数据留在它的数据卷里。"
    printf '\n'
    UNDO_SHA="$(git -C "$INSTALL_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)"
    UNDO_BRANCH="$(git -C "$INSTALL_DIR" symbolic-ref -q --short HEAD 2>/dev/null || true)"
    info "正在把代码目录切到 ${UPGRADE_REF}。"
    checkout_ref "$UPGRADE_REF"
    UPGRADE_UNDO=1
    # 剩下的是新代码的事，所以交给随那份代码来的安装脚本，它知道自己的 compose 文件
    # 需要什么。只交给懂这种交接的那份：5.1.6 的会再把代码目录挪回 main，5.1.5 的根本
    # 不认识 --upgrade，所以碰到这两个时，由这一份自己接着做。exec 不会跑 EXIT trap；
    # 撤销由下一份负责。
    local next="$INSTALL_DIR/install/$SCRIPT_NAME"
    if [ -f "$next" ] && grep -q 'DTK_UPGRADE_AT_TARGET' "$next"; then
      local args=(--upgrade)
      [ -z "$UPGRADE_TO" ] || args+=("$UPGRADE_TO")
      [ "$NO_BACKUP" = 0 ] || args+=(--no-backup)
      info "交给 $UPGRADE_REF 自带的安装脚本接着做。"
      DTK_UPGRADE_AT_TARGET=1 DTK_UPGRADE_HANDOVER=1 \
        DTK_UPGRADE_UNDO_SHA="$UNDO_SHA" DTK_UPGRADE_UNDO_BRANCH="$UNDO_BRANCH" \
        DTK_PROJECT="$PROJECT" DTK_INSTALL_DIR="$INSTALL_DIR" \
        exec bash "$next" "${args[@]}"
    fi
  else
    UNDO_SHA="${DTK_UPGRADE_UNDO_SHA:-}"
    UNDO_BRANCH="${DTK_UPGRADE_UNDO_BRANCH:-}"
    [ -z "$UNDO_SHA" ] || UPGRADE_UNDO=1
  fi

  before="$(running_version || true)"
  UNDO_ENV="$(mktemp "$INSTALL_DIR/.env.upgrade.XXXXXX")" || die "无法在 $INSTALL_DIR 里创建临时文件。"
  cat "$INSTALL_DIR/.env" >"$UNDO_ENV"
  refuse_unknown_schema "$before"
  with_target_tag validate_compose

  if [ "$USE_PUBLISHED" = 1 ]; then
    info "正在拉取。"
    local services=(api worker)
    [ "$WANT_DOWNLOADER" = 1 ] && services+=(downloader)
    # 只拉已发布的 transcriber。本地的那个由 `up` 构建，因为它要的 tag 是新的，
    # 本机上还没有。
    if [ "$WANT_TRANSCRIBER" = 1 ] && [ -n "$(env_value DTK_TRANSCRIBER_IMAGE)" ]; then
      services+=(transcriber)
    fi
    if ! with_target_tag "$CTL" pull "${services[@]}"; then
      die "拉不到 ${UPGRADE_TO}。
    去确认这个 tag 是否存在：https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api/tags
    开着转写时，早于转写功能的版本没有 transcriber 镜像；要退回那么早，
    先在菜单里把转写关掉。"
    fi
  else
    # 没什么可拉的：`pull` 会去 Docker Hub 找 dtk-app，那个仓库从来不存在，源码
    # 构建的安装以前每次升级都死在这里。它的镜像来自刚更新的代码，tag 还是原来那个，
    # 所以得靠构建让 `up` 用上新的。
    info "正在用新代码构建镜像。有缓存的话很快。"
    local builds=(api)
    [ "$WANT_DOWNLOADER" = 1 ] && builds+=(downloader)
    [ "$WANT_TRANSCRIBER" = 1 ] && builds+=(transcriber)
    "$CTL" build "${builds[@]}" || die "镜像构建失败。"
  fi

  # 浏览器镜像只在本机构建、从不拉取，所以升级时要按新代码重建它 —— 用的是那份代码
  # 写明的 CloakBrowser commit。
  settle_browser_pin
  if [ "$WANT_BROWSER" = 1 ]; then
    info "正在构建浏览器镜像。有缓存的话很快；CloakBrowser 换了版本的话要重新下载"
    info "Chromium，需要好几分钟。"
    with_target_tag "$CTL" build browser-rpc || die "浏览器镜像构建失败。"
  fi

  backup_before_upgrade

  info "正在迁移。"
  # 有正在跑的 postgres 时，就对着它跑，不去动它。`run` 会像 `up` 一样启动一个服务的
  # 依赖，并重建在新代码里配置有变化的那些：一次随后被拒绝的回退，仍然顺路把数据库
  # 重启了一遍。
  # 写成 ${deps[@]+...}，是因为 bash 3.2（macOS 自带的那个）在 set -u 下会把空数组
  # 当成未绑定的变量。
  local deps=()
  if "$CTL" ps --status running --services 2>/dev/null | grep -qx postgres; then
    deps=(--no-deps)
  fi
  if ! with_target_tag run_creating_networks "$CTL" run --rm ${deps[@]+"${deps[@]}"} migrate; then
    case "$UPGRADE_TO" in
      [0-9]*)
        if [ -n "$before" ] && version_is_newer "$before" "$UPGRADE_TO"; then
          die "$UPGRADE_TO 没法跑在这个数据库上：$before 已经把它迁移得更远了，
    而迁移不会往回走。回到 $UPGRADE_TO 的办法是重新装一份它、沿用这份 .env —— 备份只能用
    做它时的 DTK_SECRET_KEY 打开 —— 再把在 $UPGRADE_TO 上做的备份恢复进去。"
        fi ;;
    esac
    die "迁移失败。看一下上面的输出。数据库可能已经执行了其中一部分；
    如果需要回去，刚才做的那份备份就是退路。"
  fi

  if [ -n "$UPGRADE_TO" ]; then
    set_env_value DTK_IMAGE_TAG "$UPGRADE_TO"
    ok "已把 DTK_IMAGE_TAG 钉在 $UPGRADE_TO"
  fi
  # 还没有升级命令的话就给它加上，否则不动它。以前每次升级都重写它，别人在它的 compose
  # 那一行上加的东西 —— 一个指向自己覆盖文件的 -f —— 就丢了，整套服务起来时也就没有它。
  if ! grep -q '= upgrade ]' "$CTL"; then
    write_control_script
  fi
  UPGRADE_UNDO=""
  rm -f "$UNDO_ENV"

  info "正在重启。"
  run_creating_networks "$CTL" up -d --wait || die "新版本没能进入健康状态。看一下：$CTL logs api"
  ok "现在是 $(running_version || echo "${UPGRADE_TO:-新的代码}")。"
}

# 这次升级是给哪份安装的。在某份安装里面运行 —— 用它代码目录里的脚本，或者就在
# 它的目录下 —— 那就是这一份，不管它叫什么名字：项目名默认是 `dtk`，拿它去找一份
# 用别的名字部署的实例，要么找不到，要么找到别人的。
install_to_upgrade() {
  local here
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    printf '%s' "$DTK_INSTALL_DIR"
    return 0
  fi
  here="$(own_checkout || true)"
  if is_install_dir "$here"; then
    printf '%s' "$here"
  elif is_install_dir "$PWD"; then
    printf '%s' "$PWD"
  else
    find_existing_install || true
  fi
}

# 某个 dtkctl 生成时用的项目名。
project_of() {
  sed -n 's/^exec docker compose -p \([A-Za-z0-9_-]*\) .*/\1/p' "$1" | head -n 1
}

# `./dtkctl upgrade [版本]` 跑的就是这个。什么都不问：没指定版本就用最新的
# Release，和菜单给的默认值一样。
upgrade_unattended() {
  # 由把代码目录挪到这里的那份安装脚本交接过来，升到什么它已经定好了。
  if [ -n "${DTK_UPGRADE_AT_TARGET:-}" ]; then
    UPGRADE_TO="$UPGRADE_TARGET"
    UPGRADE_REF="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || true)"
    do_upgrade
    return 0
  fi
  plan_upgrade "$UPGRADE_TARGET"
  # 5.1.6 的安装脚本也会交接过来，在把代码目录挪到 main 之后，升到哪个版本已经定了
  # —— 包括"还是同一个版本"。
  if [ -z "${DTK_UPGRADE_HANDOVER:-}" ] && [ -n "$UPGRADE_TO" ]; then
    local installed
    installed="$(running_version || true)"
    # 三样都要对上，不只是运行中的版本：一次停在半路的升级，以前会让 .env 或代码目录
    # 停在另一个版本上，而这里却对那次本可以把它修好的运行说"不用升级"。
    if [ "$installed" = "$UPGRADE_TO" ] && [ "$(env_value DTK_IMAGE_TAG)" = "$UPGRADE_TO" ] \
        && checkout_is_at "$UPGRADE_REF"; then
      ok "已经是 ${UPGRADE_TO}，不用升级。"
      return 0
    fi
    case "$UPGRADE_TO" in
      [0-9]*)
        if [ -n "$installed" ] && version_is_newer "$installed" "$UPGRADE_TO"; then
          warn "$UPGRADE_TO 比正在运行的 $installed 旧。"
          info "只有它认识这个数据库经历过的每一次迁移，这才行得通。不认识的话，"
          info "升级会停在迁移那一步，并把一切恢复原样。"
        fi ;;
    esac
  fi
  do_upgrade
}

act_change_password() {
  step "修改口令"
  "$CTL" exec -T -e COLUMNS=200 api dtk user list 2>/dev/null | sed 's/^/    /' || warn "读取账号列表失败。"
  printf '\n'
  local who password
  who="$(ask_value "改哪个账号？" "admin")"
  [ -n "$who" ] || return 0
  SECRET_PROMPTED=1
  password="$(read_password_twice)" || { info "已取消。"; return 0; }
  if printf '%s' "$password" | "$CTL" exec -T api dtk user passwd "$who" --stdin; then
    ok "$who 的口令已修改。"
  else
    warn "没成功。对照上面的列表核一下账号名。"
  fi
  unset password
}

act_add_admin() {
  step "添加管理员"
  dim "没有改名这个功能：账号只能新建和重置口令，不能改名。想弃用一个名字，"
  dim "就建一个新账号，然后别再用旧的。"
  printf '\n'
  local who password
  who="$(ask_value "新账号名？" "")"
  [ -n "$who" ] || { warn "没给名字。"; return 0; }
  SECRET_PROMPTED=1
  password="$(read_password_twice)" || { info "已取消。"; return 0; }
  if printf '%s' "$password" | "$CTL" exec -T api dtk user create "$who" --role admin --stdin; then
    ok "已创建管理员 ${who}。"
  else
    warn "没成功。这个名字可能已经被占用了。"
  fi
  unset password
}

act_list_users() {
  step "账号列表"
  "$CTL" exec -T -e COLUMNS=200 api dtk user list 2>/dev/null | sed 's/^/    /' || warn "读取账号列表失败。"
}

# 下面每一个 `dtk ... list` 都带着 COLUMNS=200 跑。没有终端时，rich 按 80 列排表格，
# 放不下的单元格会折行 —— 一个备份文件名被拆成了 "dtk-backup-2026101" 和
# "8T103850Z.tar.gz" 两行，而恢复时提示要你填的正是这个名字。
act_backup() {
  step "备份"
  dim "备份文件写进备份卷，重建容器不会丢。"
  # -o 在这里不是可选项。不给它的话 CLI 会写到工作目录旁边，而容器的根文件系统
  # 按设计就是只读的，于是备份以 "Read-only file system" 失败，还没人说清原因。
  # DTK_BACKUP_DIR 在容器内部解析，这样它跟着 compose 走。
  # 单引号：DTK_BACKUP_DIR 要在容器里展开，不是在这里。
  # shellcheck disable=SC2016
  if "$CTL" exec -T api sh -c 'dtk backup create -o "$DTK_BACKUP_DIR"'; then
    printf '\n'
    # 单引号：DTK_BACKUP_DIR 要在容器里展开，不是在这里。
    # shellcheck disable=SC2016
    "$CTL" exec -T -e COLUMNS=200 api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' || true
  else
    warn "备份失败，请看上面的输出。"
  fi
}

# 恢复做的是补齐：归档里有而数据库里没有的行会加进来，已经在的东西一样都不会被覆盖。
# 它没法把一个实例带回更早的状态；恢复进一份新的空安装时，整个归档都会恢复进去。
# 提示以前说它会替换数据库，而它从来没这么做过。
act_restore() {
  step "恢复"
  local listing archive
  # 单引号：DTK_BACKUP_DIR 要在容器里展开，不是在这里。
  # shellcheck disable=SC2016
  listing="$("$CTL" exec -T -e COLUMNS=200 api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null || true)"
  if ! printf '%s' "$listing" | grep -q 'dtk-backup-'; then
    warn "备份卷里没有备份文件。"
    return 0
  fi
  printf '%s\n' "$listing" | sed 's/^/    /'
  printf '\n'
  dim "恢复会把归档里有、而这个数据库里没有的东西加进来。这里已有的东西不会被"
  dim "覆盖，所以它不会把实例回滚到以前。"
  archive="$(ask_value "备份文件（填上面列表里的名字，或容器里的路径）？" "")"
  [ -n "$archive" ] || return 0
  confirm_word "restore" || { info "已取消。"; return 0; }
  # 列表打出来的是文件名，而 CLI 会把一个光秃秃的名字按它自己的工作目录去找，而不是
  # 备份卷：照提示填一个名字，结果是 "no such file"。这个名字作为参数传给 sh，绝不拼进
  # 命令里。
  # shellcheck disable=SC2016
  "$CTL" exec -T api sh -c 'case "$1" in */*) p="$1" ;; *) p="$DTK_BACKUP_DIR/$1" ;; esac; exec dtk backup restore "$p" --yes' sh "$archive" \
    || warn "恢复失败，请看上面的输出。"
}

act_diagnose() {
  step "自检"
  local status=0
  "$CTL" exec -T api dtk diagnose 2>&1 | sed 's/^/    /' || status=$?
  # 1 是报告在说有检查没通过，它刚刚已经打出来了。
  case "$status" in
    0) : ;;
    1) warn "有些检查没通过；上面的报告写了是哪些。" ;;
    *) warn "自检没能跑起来。整套服务起着吗？${CTL} ps" ;;
  esac
}

# 按服务分别取，这样每个服务都有自己的份额。整套栈共用一个 --tail 时一共只截 80 行，
# 在一份忙碌的安装上，那 80 行全是 postgres、redis 和 worker，api 一行都没有。
act_logs() {
  step "日志"
  dim "每个服务最后 20 行。想实时跟踪其中一个：$CTL logs -f api"
  local service
  for service in $("$CTL" ps --services 2>/dev/null || true); do
    printf '\n'
    info "$service"
    "$CTL" logs --no-log-prefix --tail 20 "$service" 2>&1 | sed 's/^/      /' || true
  done
}

# 先 `up -d`，它会重建 .env 或 compose 文件有变化的那些容器 —— 光是 `restart` 会让
# 容器留着旧的环境 —— 然后再重启应用服务。不重启 postgres 和 redis，以前一个普通的
# `restart` 会把它们和其他东西一起停掉。
act_restart() {
  step "重启"
  run_creating_networks "$CTL" up -d --wait || { warn "服务没能进入健康状态。试试：${CTL} logs"; return 0; }
  local service apps=()
  for service in $("$CTL" ps --services 2>/dev/null || true); do
    case "$service" in postgres|redis) : ;; *) apps+=("$service") ;; esac
  done
  if [ "${#apps[@]}" -gt 0 ]; then
    "$CTL" restart "${apps[@]}" || warn "重启失败。"
  fi
  "$CTL" ps --format "table {{.Service}}\t{{.Status}}" 2>/dev/null || true
}

act_setting() {
  step "设置"
  dim "运行时设置存在数据库里，改完不用重启就生效。"
  dim "比如保留期：retention.request_log_days、retention.task_days。"
  printf '\n'
  local key value
  key="$(ask_value "设置项名字（留空则列出全部）？" "")"
  if [ -z "$key" ]; then
    # 全部列出，并且绝不接 `| head`：这个列表有好几百行，head 读到 80 行就不读了，
    # 发送列表的那一端收到 SIGPIPE，在 pipefail 下整个脚本一声不吭地就退出了。
    "$CTL" exec -T -e COLUMNS=200 api dtk config list 2>&1 | sed 's/^/    /' || true
    return 0
  fi
  "$CTL" exec -T api dtk config get "$key" 2>&1 | sed 's/^/    /' || { warn "没有这个设置项。"; return 0; }
  printf '\n'
  value="$(ask_line "新的值（留空则不改）？")"
  [ -n "$value" ] || return 0
  ask_yes_no "把 $key 设成这个值吗？" "n" || { info "保持原样。"; return 0; }
  # 值从标准输入传，不走 argv：只要 `docker exec` 还在跑，argv 就一直在宿主机的进程
  # 列表里，而其中有些是凭据 —— webhook 的密钥、notify.channels 里的 token。加 --yes，
  # 是因为这里刚刚问过了：CLI 对敏感设置项自己的那个提问没有换行，下面的 sed 就把它
  # 扣着不放，菜单看起来像卡住了。
  if "$CTL" exec -T api dtk config set --help 2>/dev/null | grep -q -- '--stdin'; then
    printf '%s\n' "$value" | "$CTL" exec -T api dtk config set "$key" --stdin --yes 2>&1 | sed 's/^/    /' \
      || warn "被拒绝了。上面的校验信息说明了原因。"
  else
    # 有 --stdin 之前的版本。值照它那里一直以来的做法放在 argv 上；升级之后就改走
    # 标准输入。
    "$CTL" exec -T api dtk config set "$key" "$value" --yes 2>&1 | sed 's/^/    /' \
      || warn "被拒绝了。上面的校验信息说明了原因。"
  fi
  unset value
}

# 这份安装里没有任何东西在用的镜像，每行一个 "ref id"。
stale_images() {
  # 「在用」= 被任何一个存在的容器引用，无论它在跑还是停着 —— 不是「和 .env 里的
  # tag 对得上」。从源码构建的安装 .env 里压根没有 tag，用那个当过滤条件时，
  # 这里会提议删掉正在服务的那个镜像。栈开着时 Docker 会拒绝，栈一停就照删不误。
  #
  # 同样保留的还有：这份安装的配置里点名的每一个镜像，不管有没有对应的容器 —— 用
  # `down` 停掉的栈一个容器都没有，否则它 6 GB 的浏览器镜像就会被删掉。本机构建的镜像，
  # 只有 Compose 给它打了"为这个项目构建"的标签才会列出来：这些名字和项目名无关，
  # 别的安装构建出来的看上去就像这份的。已发布的镜像只要没东西在用就会列出来；它们
  # 可以重新拉。
  local in_use configured
  in_use="$(docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)"
  configured="$("$CTL" config --images 2>/dev/null | sort -u || true)"
  # 这里的 IFS 是换行和制表符，所以 `read ref id` 得被告知两个字段之间隔的是空格。
  # 不告诉它的话，整行都进了 ref，什么都匹配不上，每个镜像都被当成没人用。
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null \
    | grep -E '^(evil0ctal/douyin_tiktok_download_api|dtk-browser-rpc|dtk-app|dtk-downloader|dtk-transcriber)' \
    | while IFS=' ' read -r ref id; do
        printf '%s\n' "$in_use" | grep -qxF "$ref" && continue
        printf '%s\n' "$configured" | grep -qxF "$ref" && continue
        case "$ref" in
          evil0ctal/*) : ;;
          *) [ "$(docker image inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id" 2>/dev/null || true)" = "$PROJECT" ] || continue ;;
        esac
        printf '%s %s\n' "$ref" "$id"
      done || true
}

# 磁盘。这里真正在涨的是镜像和构建缓存，不是日志：每个服务都把 json-file 钉在
# 10m × 3，整套栈的容器日志过不了 180 MB 左右。旧的镜像 tag 和 buildx 缓存
# 没有这个上限。
#
# 刻意只针对本项目。直接 `docker image prune -a` 会把同一台机器上别的栈的镜像
# 一起端掉，那不是这个脚本该替人做的决定。
act_free_disk() {
  step "释放磁盘空间"
  info "清理前："
  docker system df 2>/dev/null | sed 's/^/      /'

  printf '\n'
  dim "容器日志已经被 compose 文件按每服务 10 MB × 3 封顶了。真正没有上限、"
  dim "会一直涨的是旧的镜像 tag 和构建缓存。"
  printf '\n'

  local images
  images="$(stale_images)"

  if [ -n "$images" ]; then
    info "这份安装里没有任何东西在用的镜像："
    printf '%s\n' "$images" | sed 's/^/      /'
    printf '\n'
    if ask_yes_no "删掉它们吗？" "y"; then
      printf '%s\n' "$images" | while IFS=' ' read -r ref id; do
        docker rmi "$ref" >/dev/null 2>&1 && info "已删除 $ref" || true
      done
    fi
  else
    ok "这份安装没有闲置的镜像。"
  fi

  printf '\n'
  dim "构建缓存能极大加快浏览器镜像的重建 —— 小改动后重建只要几秒，全靠它。"
  dim "清掉就没这个便宜了。"
  dim "它是 Docker 的，这台机器上构建的所有东西共用，不只属于这份安装。"
  if ask_yes_no "清掉整台机器的 Docker 构建缓存吗？" "n"; then
    docker builder prune -af 2>&1 | tail -2 | sed 's/^/    /' || true
  fi

  printf '\n'
  info "清理后："
  docker system df 2>/dev/null | sed 's/^/      /'
}

# 转写是大多数安装事后才想加的那个可选组件，所以菜单能开关的就是它。两个方向都
# 只改 .env 和 dtkctl 里属于它的那几行，然后重建整套服务：api 和 worker 只在
# 启动时读一次 transcriber 的地址。
act_transcription() {
  step "转写"
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    ok "已开启。模型：${MODEL_PATH:-.env 里没有记录}"
    "$CTL" ps transcriber --format "table {{.Service}}\t{{.Status}}" 2>/dev/null | sed 's/^/    /' || true
    printf '\n'
    dim "关掉之后，已经做好的转写都还在，模型也留在磁盘上。"
    if ! ask_yes_no "关闭转写吗？" "n"; then
      return 0
    fi
    "$CTL" rm -sf transcriber >/dev/null 2>&1 || true
    set_env_value DTK_TRANSCRIBER_URL ""
    set_ctl_profile transcriber off
    WANT_TRANSCRIBER=0
    "$CTL" up -d --wait || { warn "服务没能恢复到健康状态。试试：${CTL} logs"; return 0; }
    ok "转写已关闭。想腾出空间，请自己删掉 ${MODEL_PATH:-模型目录}。"
    return 0
  fi

  if [ "$WANT_DOWNLOADER" = 0 ]; then
    warn "转写处理的是已存的视频，而这份安装没开媒体下载。"
    return 0
  fi
  if [ "$CPUS" -lt 2 ]; then
    warn "转写至少要两个核，这台机器只有一个。"
    return 0
  fi
  dim "离线转写在这台机器上把已存的视频转成文字，用的是针对普通话训练的 FunASR"
  dim "模型。工作时约占 1 GiB 内存和两个核，另需 500 MB 模型，脚本可以替你下载并校验。"
  if ! ask_yes_no "开启转写吗？" "y"; then
    return 0
  fi
  WANT_TRANSCRIBER=1
  choose_model_path
  fetch_models
  if [ "$WANT_TRANSCRIBER" = 0 ]; then
    return 0
  fi

  # 先弄到镜像再改文件：早于转写功能的版本没有 transcriber 的 tag，等 .env
  # 已经写成"开启"才发现，就会留下一份起不来的安装。
  local image
  image="$(env_value DTK_TRANSCRIBER_IMAGE)"
  if [ -z "$image" ] && [ "$USE_PUBLISHED" = 1 ]; then image="$TRANSCRIBER_IMAGE"; fi
  if [ -n "$image" ]; then
    info "正在拉取转写镜像。"
    if ! DTK_TRANSCRIBER_IMAGE="$image" "$CTL" --profile transcriber pull transcriber; then
      warn "拉取失败。可能是这份安装跑的版本早于转写功能（先在主菜单选 2 升级），"
      warn "也可能是镜像仓库没有响应。"
      WANT_TRANSCRIBER=0
      return 0
    fi
  else
    info "正在构建转写镜像。"
    if ! "$CTL" --profile transcriber build transcriber; then
      warn "转写镜像构建失败。什么都没改。"
      WANT_TRANSCRIBER=0
      return 0
    fi
  fi

  write_transcriber_env
  set_ctl_profile transcriber on
  info "正在启动它，并重建 api 和 worker，让它们知道它在哪。"
  if ! "$CTL" up -d --wait; then
    # 关回去，而不是让它开着：一个不健康的 transcriber 会让之后每一次 `up -d --wait`
    # 都失败，包括升级的最后一步，而那一步是在迁移之后才跑的。
    warn "它没能进入健康状态，所以转写重新关闭。原因通常在"
    warn "transcriber 的日志里："
    "$CTL" logs --no-log-prefix --tail 15 transcriber 2>&1 | sed 's/^/      /' || true
    "$CTL" rm -sf transcriber >/dev/null 2>&1 || true
    set_env_value DTK_TRANSCRIBER_URL ""
    set_ctl_profile transcriber off
    WANT_TRANSCRIBER=0
    "$CTL" up -d --wait || warn "服务也没能恢复到健康状态。试试：${CTL} logs"
    return 0
  fi
  ok "转写已开启。在控制台里打开一个已存的视频，选「转写视频」即可。"
}

# ------------------------------------------------------------------ 菜单 --

# 做了点什么就返回 0 —— 调用方随后退出 —— 选「返回」则返回 1。
menu_uninstall() {
  while true; do
    step "停止或移除"
    info "  1  停掉服务，数据一个字节都不动"
    info "  2  停掉并删除数据卷 —— 数据库、媒体、备份"
    info "  3  在 2 的基础上，连 $INSTALL_DIR 一起删掉"
    info "  b  返回"
    case "$(ask_choice "选哪个？")" in
      # 用 `stop`，不用 `down`：down 会删掉容器，而容器上的标签正是这个脚本找到一份
      # 安装的办法。down 之后，下一次运行什么都找不到，就开始在它上面重新安装。
      1) "$CTL" stop && ok "已停止。要再启动：$CTL up -d" ; return 0 ;;
      2)
        warn "这会删掉数据库、媒体卷，以及里面的每一份备份。"
        dim "想保留备份，先把它们拷出来：$CTL cp api:/var/lib/dtk/backups ./backups"
        confirm_word "delete" || { info "已取消。"; continue; }
        "$CTL" down -v --remove-orphans || die "移除整套服务失败，见上面的输出。别的什么都没删。"
        ok "已停止，数据卷已删除。"
        return 0 ;;
      3)
        warn "这会删掉数据卷，还会删掉 ${INSTALL_DIR}，包括你的 .env。"
        warn "反正没有那个 .env，数据本来也解不开。"
        dim "想保留备份，先把它们拷出来：$CTL cp api:/var/lib/dtk/backups ~/dtk-backups"
        confirm_word "delete" || { info "已取消。"; continue; }
        # 失败就停在这里：一次失败的 `down -v` 之后再删目录，会留下数据卷和容器，
        # 而唯一能打开它们的 .env 已经没了。
        "$CTL" down -v --remove-orphans || die "移除整套服务失败，见上面的输出。${INSTALL_DIR} 原样留着。"
        # 不信任变量，而是加护栏：这是整个脚本唯一一处删目录树的地方，
        # 空值或者离谱的值绝不能走到 rm 那一步。
        case "$INSTALL_DIR" in
          ""|"/"|"/usr"|"/etc"|"/var"|"/home"|"/root"|"/opt")
            die "拒绝删除 ${INSTALL_DIR}。" ;;
        esac
        [ -f "$INSTALL_DIR/docker/compose.yml" ] || die "$INSTALL_DIR 看着不像一份安装，不删。"
        rm -rf -- "$INSTALL_DIR" && ok "已删除 ${INSTALL_DIR}。"
        return 0 ;;
      b|"") return 1 ;;
      *) warn "请选 1、2、3 或 b。" ;;
    esac
  done
}

menu_manage() {
  while true; do
    step "管理"
    info "  1  修改口令                6  自检"
    info "  2  添加管理员              7  日志"
    info "  3  账号列表                8  重启服务"
    info "  4  立即备份                9  设置"
    info "  5  从备份恢复             10  释放磁盘空间"
    info "                            11  开启或关闭转写"
    info "  b  返回"
    case "$(ask_choice "选哪个？")" in
      1) act_change_password; pause_for_reader ;;
      2) act_add_admin; pause_for_reader ;;
      3) act_list_users; pause_for_reader ;;
      4) act_backup; pause_for_reader ;;
      5) act_restore; pause_for_reader ;;
      6) act_diagnose; pause_for_reader ;;
      7) act_logs; pause_for_reader ;;
      8) act_restart; pause_for_reader ;;
      9) act_setting; pause_for_reader ;;
      10) act_free_disk; pause_for_reader ;;
      11) act_transcription; pause_for_reader ;;
      b|"") return 0 ;;
      *) warn "请从上面挑一个编号，或者 b。" ;;
    esac
  done
}

menu_existing() {
  local installed latest upgrade_line=""
  installed="$(running_version || true)"
  latest="$(latest_release)"

  step "这里已经有一份安装了"
  ok "$INSTALL_DIR"
  if [ -n "$installed" ]; then
    ok "运行中：$installed"
  else
    warn "没在跑，或者 api 还没开始应答。"
  fi
  if [ -n "$latest" ] && [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    upgrade_line="$latest"
    warn "有新版本 ${latest}。"
  fi

  while true; do
    printf '\n'
    info "  1  状态 —— 版本、容器、磁盘"
    if [ "$USE_PUBLISHED" = 0 ]; then
      info "  2  更新代码并重新构建"
    elif [ -n "$upgrade_line" ]; then
      info "  2  升级到 $upgrade_line"
    else
      info "  2  重装当前版本（拉取、迁移、重启）"
    fi
    info "  3  管理 —— 口令、备份、设置、磁盘"
    info "  4  停止或移除这份安装"
    info "  q  退出"
    case "$(ask_choice "选哪个？")" in
      1) show_status; pause_for_reader ;;
      2)
        local target current
        current="$(env_value DTK_IMAGE_TAG)"
        if [ "$USE_PUBLISHED" = 0 ]; then
          # 源码构建，跑的就是代码目录里的那份。以前这里输入的 tag 会被当成
          # dtk-app 去 Docker Hub 拉，必然失败。
          plan_upgrade ""
          do_upgrade
          pause_for_reader
          continue
        fi
        if [ -n "$upgrade_line" ]; then
          target="$(ask_value "升到哪个版本？" "${upgrade_line#v}" valid_target)"
        else
          # 没有更新的 Release，不等于无处可去。钉在 sha- 构建上的实例 —— 自述文档
          # 就是这么建议生产环境做的 —— 否则会永远停在这里：latest 跟着 Release 走，
          # 而 Release 得有人去发。同一个输入框也能填一个更旧的版本，最远能退到最后一个
          # 不缺这个数据库任何迁移的版本。
          dim "没有更新的 Release。你仍然可以换到另一个已发布的版本：比如 5.1.6"
          dim "这样的版本号、某个 sha-… 构建，或者 latest。"
          target="$(ask_value "升到哪个版本？" "${current:-latest}" valid_target)"
        fi
        [ -n "$target" ] || continue
        plan_upgrade "$target"
        if [ "$UPGRADE_TO" = "$current" ]; then
          info "本来就是 ${UPGRADE_TO}。仍然拉取、迁移并重启一次。"
        fi
        do_upgrade
        pause_for_reader ;;
      3) menu_manage ;;
      4) if menu_uninstall; then return 0; fi ;;
      q|"") return 0 ;;
      *) warn "请选 1、2、3、4 或 q。" ;;
    esac
  done
}

# ------------------------------------------------------------------ 主流程 --

usage() {
  cat <<EOF
用法：bash install.zh.sh [选项]

  --yes, -y      所有问题都取默认答案。发布在 127.0.0.1:8000，开下载器，
                 不建浏览器容器，不开转写。
  --check        只探测系统并打印将会发生什么，然后停下。不改任何东西。
  --manage       直接进入已有安装的管理菜单。
  --upgrade [版本]
                 什么都不问，直接升级已有的安装：升到最新的 Release，或者
                 升到指定的版本（5.1.6、5.1、latest 或 sha-<commit>）。
                 ./dtkctl upgrade 跑的就是它。迁移之前会先做一次备份；
                 整套服务切换之前出了错，会把代码目录和 .env 放回原样。
  --no-backup    配合 --upgrade：跳过那次备份。
  --help, -h     这段说明。

不带任何选项运行时它会自己判断该做哪件事：没装过就安装，装过就打开菜单 ——
查看状态、升级、改口令、备份、改设置、清磁盘。

环境变量（每一个都能预设一个答案，配合 --yes 就是无人值守安装）：
  DTK_PROJECT            Compose 项目名。默认：dtk。
  DTK_INSTALL_DIR        装到哪里。默认：root 是 /opt/dtk，否则 ~/dtk。
  DTK_REPO_URL           从哪里克隆，用于镜像源。默认：GitHub。
  DTK_GH_PROXY           加在克隆用的 GitHub 地址前的 https:// 镜像前缀，默认不加。
                         这两个都等于把整个安装交给镜像，所以克隆之前会先问你。
  DTK_VERSION            要安装的版本：5.1.6、5.1、latest（默认）或 sha-<commit>。
                         检出的就是它的 tag。
  DTK_BIND_HOST         127.0.0.1（默认）或 0.0.0.0。
  DTK_BIND_PORT          默认：8000。
  DTK_ENABLE_BROWSER     1 构建浏览器容器，0（默认）不建。
  DTK_ENABLE_DOWNLOADER  1（默认）启用媒体下载，0 不启用。
  DTK_ENABLE_TRANSCRIBER 1 启用离线转写，0（默认）不启用。需要开着媒体下载、
                         至少两个核。
  DTK_TRANSCRIBER_MODEL_PATH
                         模型所在的目录，或者要下载到的目录。
                         默认：<安装目录>/models/funasr-onnx。
  CLOAKBROWSER_COMMIT    钉住浏览器镜像安装的 CloakBrowser commit。
                         默认：不设置，跟随那个版本的 docker/compose.yml，
                         并随升级移动。
  DTK_USE_PUBLISHED      1（默认）拉已发布镜像，0 从源码构建。
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)   ASSUME_YES=1 ;;
      --check)    CHECK_ONLY=1 ;;
      --manage)   MANAGE_ONLY=1 ;;
      --upgrade)  UPGRADE_ONLY=1 ;;
      --no-backup) NO_BACKUP=1 ;;
      -h|--help)  usage; exit 0 ;;
      -*)         die "无法识别的选项：$1（试试 --help）" ;;
      # --upgrade 可以带的版本号，放在哪都行：`upgrade 5.1.6 --no-backup` 和
      # `upgrade --no-backup 5.1.6` 是同一个意思。
      *)
        if [ "$UPGRADE_ONLY" = 1 ] && [ -z "$UPGRADE_TARGET" ]; then
          UPGRADE_TARGET="$1"
        else
          die "多余的参数：$1（试试 --help）"
        fi ;;
    esac
    shift
  done

  # `./dtkctl upgrade` 是在一份运行中的安装里敲的命令，不是安装：
  # 横幅和本机报告在那里只是噪音。
  if [ "$UPGRADE_ONLY" = 0 ]; then
    banner
  fi
  detect_os
  detect_resources
  if [ "$UPGRADE_ONLY" = 0 ]; then
    report_host
  fi

  step "检查 Docker"
  if ! check_docker_present; then
    if [ "$CHECK_ONLY" = 1 ]; then
      warn "没有装 Docker。"
      if convenience_script_supported; then
        info "正式运行时，脚本会提议替你跑 Docker 官方安装脚本。"
      fi
      info "这个系统上的安装方法："
      dim  "  $(docker_install_hint)"
      exit 0
    fi
    offer_docker_install
  fi
  ok "Docker $(docker --version 2>/dev/null | sed 's/Docker version //; s/,.*//')"

  if [ "$CHECK_ONLY" = 1 ]; then
    check_compose || true
    if check_docker_running; then ok "Docker 守护进程可以连上。"; else warn "Docker 守护进程连不上。"; fi
    step "仅检查"
    info "什么都没改。去掉 --check 才会真正安装。"
    exit 0
  fi

  ensure_docker_running
  check_compose || die "需要 Compose v2 $COMPOSE_MIN 或更新的版本。"
  detect_docker_resources

  if [ "$UPGRADE_ONLY" = 1 ]; then
    INSTALL_DIR="$(install_to_upgrade)"
    if [ -z "$INSTALL_DIR" ] || [ ! -x "$INSTALL_DIR/dtkctl" ]; then
      die "找不到项目 '$PROJECT' 的安装。
    把 DTK_INSTALL_DIR 设成它所在的目录，或者到那个目录里运行 ./dtkctl upgrade。"
    fi
    CTL="$INSTALL_DIR/dtkctl"
    PROJECT="$(project_of "$CTL")"
    [ -n "$PROJECT" ] || die "没能从 $CTL 里读出项目名。"
    load_install_state
    upgrade_unattended
    exit 0
  fi

  # 已经装过的话，这个脚本就从"安装器"变成"管理工具"。每次都要找 —— 带 --yes 和
  # DTK_INSTALL_DIR 时也找，以前这两种情况会跳过查找、直接装在它上面：在
  # DTK_INSTALL_DIR 里找，从 Docker 给它的容器打的标签里找（所以不管当初装在哪都能
  # 找到），也在这个脚本会建议的那个目录里找。
  # 建议的那个目录只有装的是这个项目才算：不然 DTK_PROJECT=dtk2 会在那里找到 dtk 的安装、
  # 打开它的菜单。
  local found="" preset=""
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    preset="$(absolute_path "$(expand_tilde "$DTK_INSTALL_DIR")")"
    [ -x "$preset/dtkctl" ] && found="$preset"
  fi
  if [ -z "$found" ] && [ -z "$preset" ]; then
    # 这个脚本所在的代码目录，或者它运行时所在的目录，只要那是一份安装：在一份安装里跑
    # `bash install/install.zh.sh --manage` 管理的就是那一份，不管它的项目叫什么 —— 脚本给出的
    # 提示就是这么说的，而项目名不是 dtk 时它什么都找不到。
    local here
    here="$(own_checkout || true)"
    if is_install_dir "$here"; then
      found="$here"
    elif is_install_dir "$PWD"; then
      found="$PWD"
    fi
  fi
  [ -n "$found" ] || found="$(find_existing_install || true)"
  if [ -z "$found" ] && [ -z "$preset" ]; then
    local suggested="$HOME/dtk"
    [ "$(id -u)" = "0" ] && suggested="/opt/dtk"
    if is_install_dir "$suggested" && [ "$(project_of "$suggested/dtkctl")" = "$PROJECT" ]; then
      found="$suggested"
    fi
  fi
  if [ "$MANAGE_ONLY" = 1 ] && [ -z "$found" ]; then
    die "没有找到项目 '${PROJECT}' 的安装。
    去掉 --manage 可以新建一个；或者用 DTK_INSTALL_DIR 指定它在哪。"
  fi
  if [ -n "$found" ]; then
    INSTALL_DIR="$found"
    CTL="$found/dtkctl"
    PROJECT="$(project_of "$CTL")"
    [ -n "$PROJECT" ] || die "没能从 $CTL 里读出项目名。"
    load_install_state
    if [ -z "$TTY_IN" ] || { [ "$ASSUME_YES" = 1 ] && [ "$MANAGE_ONLY" = 0 ]; }; then
      ok "${INSTALL_DIR} 已经有一份安装了；什么都没改。"
      info "升级它：$CTL upgrade"
      info "在终端里管理它：bash $INSTALL_DIR/install/$SCRIPT_NAME --manage"
      exit 0
    fi
    # 菜单里的每个决定都要问人。--yes 会在没人看见的情况下替人回答：「释放磁盘空间」
    # 里的「删掉它们吗？」直接照做了，而「恢复」却悄悄什么都没做。
    ASSUME_YES=0
    menu_existing
    exit 0
  fi

  if [ -z "$TTY_IN" ] && [ "$ASSUME_YES" = 0 ]; then
    die "没有终端可以提问。
    要么把脚本下载下来再运行：
      curl -fsSL $REPO_RAW/install/install.zh.sh -o install.zh.sh && bash install.zh.sh
    要么接受全部默认值：
      curl -fsSL $REPO_RAW/install/install.zh.sh | bash -s -- --yes"
  fi

  choose_directory
  refuse_foreign_volumes
  prepare_directory
  ask_questions
  fetch_models
  write_env
  write_host_override
  write_control_script
  CTL="${INSTALL_DIR}/dtkctl"
  validate_compose
  bring_up
  show_result
}

main "$@"
