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

readonly REPO_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git"
readonly REPO_RAW="https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main"
readonly DOCKER_INSTALL_URL="https://get.docker.com"
readonly DOCS_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/zh/02-installation.md"

# Compose 从 2.24 才有长格式的 `env_file:` 和 COMPOSE_ENV_FILES，而
# docker/compose.yml 两个都用到了。更老的版本会以一种看起来像"这个项目有问题"
# 的方式失败。
readonly COMPOSE_MIN="2.24"

# 可以覆盖，这样试跑时不会碰到真正在用的 `dtk` 那套。做了校验，因为这个值会被写进
# 生成的 dtkctl 里 —— 不是纯名字的东西到了那里就变成 shell 语法了。
PROJECT="${DTK_PROJECT:-dtk}"
case "$PROJECT" in
  ''|*[!a-zA-Z0-9_-]*)
    printf '错误：DTK_PROJECT 只能是字母、数字、短横线或下划线。\n' >&2
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

# 凡是建在安装目录之外的东西都登记在这里，这样中途 Ctrl-C 也不会留下垃圾。
TMP_FILES=()
cleanup() {
  local f
  for f in "${TMP_FILES[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done
}
trap cleanup EXIT INT TERM

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
    dim "sudo $*"
    sudo "$@"
  fi
}

# ------------------------------------------------------------------ 探测 --

OS=""          # linux | macos | wsl | unsupported（不支持）
DISTRO=""      # debian、ubuntu、fedora、rhel、arch、alpine、opensuse …
DISTRO_LIKE="" # os-release 里的 ID_LIKE
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
      info "升级 Docker 之后再跑一次。见 $DOCS_URL"
      return 1
    fi
    ok "Docker Compose $raw"
    return 0
  fi
  if have docker-compose; then
    warn "检测到的是老的独立版 docker-compose（v1）。"
    info "这套栈用到了 Compose v2 的特性。装一个当前版本的 Docker 即可，"
    info "它自带 Compose 插件：$DOCKER_INSTALL_URL"
    return 1
  fi
  warn "没有装 Docker Compose v2。"
  return 1
}

docker_install_hint() {
  local family
  family="$(distro_family)"
  case "$family" in
    debian) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2" ;;
    rhel)   printf '%s' "sudo dnf install -y docker docker-compose-plugin  # 老系统用 yum" ;;
    suse)   printf '%s' "sudo zypper install -y docker docker-compose" ;;
    arch)   printf '%s' "sudo pacman -S --needed docker docker-compose" ;;
    alpine) printf '%s' "sudo apk add docker docker-cli-compose && sudo rc-update add docker default" ;;
    nixos)  printf '%s' "在 configuration.nix 里加 virtualisation.docker.enable = true;" ;;
    void)   printf '%s' "sudo xbps-install -S docker docker-compose" ;;
    gentoo) printf '%s' "sudo emerge app-containers/docker app-containers/docker-compose" ;;
    *)      printf '%s' "见 https://docs.docker.com/engine/install/" ;;
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

# 官方的便捷安装脚本支不支持这个发行版。它覆盖 Debian、RHEL、SUSE 三系；
# 在 Arch 和 Alpine 上它会直接拒绝，而让人去管道执行一个注定会拒绝他的脚本，
# 比直接告诉他包管理器的那一条命令要糟糕。
convenience_script_supported() {
  case "$(distro_family)" in
    debian|rhel|suse) return 0 ;;
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
  info "  1. 用你的包管理器："
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
      die "请用上面那条命令装好 Docker，然后再跑一次这个脚本。"
    fi
  else
    info ""
    info "  2. Docker 官方安装脚本不支持 ${DISTRO_NAME}，所以请用上面那条"
    info "     包管理器命令。"
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

# ------------------------------------------------------------------ 步骤 --

# 每个问题的答案都能用环境变量预设 —— 这才让 --yes 成为一次完整的无人值守安装，
# 而不只是"全取默认值"。没设的项一律落到"替一个不在场的人做决定时最稳妥"的那个值。
INSTALL_DIR=""
BIND_HOST="${DTK_BIND_HOST:-127.0.0.1}"
BIND_PORT="${DTK_BIND_PORT:-8000}"
WANT_BROWSER="${DTK_ENABLE_BROWSER:-0}"
WANT_DOWNLOADER="${DTK_ENABLE_DOWNLOADER:-1}"
USE_PUBLISHED="${DTK_USE_PUBLISHED:-1}"

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

  # 开头的 ~ 得自己展开；从 `read` 拿到的是字面量。
  # 这里的 ~ 是字面量：`read` 不做展开，它是以一个字符的形式进来的，
  # 所以这是模式匹配，不是在尝试展开。
  # shellcheck disable=SC2088
  case "$INSTALL_DIR" in
    "~") INSTALL_DIR="$HOME" ;;
    "~/"*) INSTALL_DIR="$HOME/${INSTALL_DIR#\~/}" ;;
  esac
  INSTALL_DIR="$(absolute_path "$INSTALL_DIR")"

  case "$INSTALL_DIR" in
    ""|"/"|"/usr"|"/etc"|"/var"|"/bin"|"/sbin"|"/lib"|"/boot"|"/home"|"/root")
      die "拒绝安装到 ${INSTALL_DIR}。" ;;
  esac
}

prepare_directory() {
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR 已存在，而且不是目录。"
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    local origin
    origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "")"
    case "$origin" in
      *Douyin_TikTok_Download_API*)
        ok "在 $INSTALL_DIR 发现已有的安装。"
        info "你的 .env 和 Docker 数据卷不会被动。"
        if ask_yes_no "把代码更新到最新的 main 吗？" "y"; then
          git -C "$INSTALL_DIR" fetch --quiet origin main
          git -C "$INSTALL_DIR" checkout --quiet main 2>/dev/null || true
          # 用 --ff-only，绝不用 --hard：reset 会把别人有意做的改动扔掉，
          # 这个脚本没有资格替人做这个决定。
          git -C "$INSTALL_DIR" merge --ff-only --quiet origin/main \
            || warn "本地有改动，保持原样不动。"
        fi
        return
        ;;
      *)
        die "$INSTALL_DIR 是别的项目的 git 仓库（${origin}）。
    请换一个空目录。" ;;
    esac
  fi

  if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    die "$INSTALL_DIR 非空，而且不是已有的安装。请换一个目录。"
  fi

  step "获取源码"
  have git || die "没有装 git。用 $(git_install_hint) 装上，然后再跑一次。"

  local parent
  parent="$(dirname "$INSTALL_DIR")"
  if [ ! -d "$parent" ]; then
    as_root mkdir -p "$parent"
  fi
  if [ ! -w "$parent" ]; then
    as_root mkdir -p "$INSTALL_DIR"
    as_root chown "$(id -u):$(id -g)" "$INSTALL_DIR"
  fi

  git clone --quiet --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR" \
    || die "克隆 $REPO_URL 失败。检查一下网络；在中国大陆多半要先换源：$DOCS_URL"
  ok "已克隆到 $INSTALL_DIR"
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

  printf '\n'
  dim "下载器把媒体存到本机的 Docker 数据卷里。它很小很省；不开的话，下载请求"
  dim "会返回 501 并说明原因。"
  local dl_default="y"; [ "$WANT_DOWNLOADER" = 0 ] && dl_default="n"
  if ask_yes_no "启用媒体下载吗？" "$dl_default"; then WANT_DOWNLOADER=1; else WANT_DOWNLOADER=0; fi

  printf '\n'
  dim "用已发布的镜像，拉下来一分钟左右就能起。从源码构建要好几分钟，而且需要"
  dim "npm 和 uv 都能连上网。"
  local pub_default="y"; [ "$USE_PUBLISHED" = 0 ] && pub_default="n"
  if ask_yes_no "用已发布的镜像（推荐）？" "$pub_default"; then USE_PUBLISHED=1; else USE_PUBLISHED=0; fi

}

write_env() {
  step "写 .env"
  local env_file="$INSTALL_DIR/.env"

  if [ -f "$env_file" ]; then
    ok "保留已有的 .env —— 现在的数据库就是用里面那些密钥建起来的。"
    info "想重新生成就自己删掉它，但那样旧的数据卷就打不开了。"
    return
  fi

  local pg_pass redis_pass secret dl_token
  pg_pass="$(random_hex 24)"
  redis_pass="$(random_hex 24)"
  secret="$(random_b64 48)"
  dl_token="$(random_hex 24)"

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
    printf '\n'
    printf 'DTK_BIND_HOST=%s\n' "$BIND_HOST"
    printf 'DTK_BIND_PORT=%s\n' "$BIND_PORT"
    if [ "$USE_PUBLISHED" = 1 ]; then
      printf 'DTK_IMAGE=evil0ctal/douyin_tiktok_download_api\n'
      printf 'DTK_IMAGE_TAG=latest\n'
      printf 'DTK_DOWNLOADER_IMAGE=evil0ctal/douyin_tiktok_download_api-downloader\n'
    fi
  } >"$env_file"
  umask "$old_umask"
  chmod 600 "$env_file"
  ok "已写入 ${env_file}（仅属主可读）。"
}

# 容器要的核数超过宿主机实际核数时，Docker 会直接拒绝启动 —— 是硬报错，不是变慢；
# 而在小机器上，仓库自带的内存上限加起来比实际内存还多。所以比"默认值所针对的机器"
# 更小的宿主机，会拿到一份属于它自己的覆盖文件。
write_host_override() {
  local override="$INSTALL_DIR/compose.host.yml"
  local need_cpu=0 need_mem=0

  [ "$CPUS" -lt 4 ] && need_cpu=1
  [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 7800 ] && need_mem=1

  if [ "$need_cpu" = 0 ] && [ "$need_mem" = 0 ]; then
    rm -f "$override"
    return
  fi

  step "按这台机器调整资源上限"
  local api_cpu browser_cpu
  api_cpu="$CPUS"; [ "$api_cpu" -gt 2 ] && api_cpu=2
  browser_cpu="$CPUS"; [ "$browser_cpu" -gt 4 ] && browser_cpu=4
  # 至少留半个核：浏览器把每个核都占死的话，铸造期间还要响应请求的 API 就饿死了。
  if [ "$CPUS" -le 2 ]; then api_cpu="1.5"; browser_cpu="1.5"; fi

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
      printf '  postgres:\n    mem_limit: %sm\n' "$(( MEM_MIB / 4 ))"
      printf '  redis:\n    mem_limit: %sm\n' "$(( MEM_MIB / 12 ))"
      printf '  api:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 9 ))" "$api_cpu"
      printf '  worker:\n    mem_limit: %sm\n' "$(( MEM_MIB / 9 ))"
      printf '  browser-rpc:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 3 ))" "$browser_cpu"
      printf '  downloader:\n    mem_limit: %sm\n    cpus: 1.0\n' "$(( MEM_MIB / 20 ))"
    else
      printf '  api:\n    cpus: %s\n' "$api_cpu"
      printf '  worker:\n    cpus: %s\n' "$api_cpu"
      printf '  browser-rpc:\n    cpus: %s\n' "$browser_cpu"
      printf '  downloader:\n    cpus: 1.0\n'
    fi
  } >"$override"
  ok "已写入 compose.host.yml —— 上限已按 $CPUS 核 / $MEM_MIB MiB 缩放。"
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
    printf '#\n'
    printf '# 它替你带上的三样最容易忘的东西：项目名、本机资源覆盖文件，以及\n'
    printf '# COMPOSE_ENV_FILES —— 没有它，compose 会去找 docker/.env，然后\n'
    printf '# 悄悄用默认值做插值。\n'
    printf 'set -euo pipefail\n'
    # 单引号是故意的：这些要在 dtkctl 运行时展开，不是现在。
    # shellcheck disable=SC2016
    printf 'cd "$(dirname "$0")"\n'
    printf 'export COMPOSE_ENV_FILES=.env\n'
    printf 'exec docker compose -p %s %s' "$PROJECT" "$files"
    [ "$WANT_BROWSER" = 1 ] && printf ' --profile browser'
    [ "$WANT_DOWNLOADER" = 1 ] && printf ' --profile downloader'
    printf ' "$@"\n'
  } >"$ctl"
  chmod 755 "$ctl"
  ok "已写入 dtkctl —— 之后用它，别直接敲 docker compose。"
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

  info "正在执行数据库迁移。"
  "$ctl" run --rm migrate || die "迁移失败，请看上面的输出。"

  info "正在启动各个服务。"
  "$ctl" up -d --wait || die "服务没有全部进入健康状态。看一下：$ctl logs"
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
  info "文档     $DOCS_URL"
  if [ "$WANT_BROWSER" = 0 ]; then
    printf '\n'
    dim "浏览器容器没开，所以身份池不会自己补。"
    dim "在控制台里导入你自己的 Cookie：身份池 → 导入。"
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

# 已有部署在哪 —— 问 Docker，不靠猜。Compose 记录了它是从哪个配置文件起来的，
# 安装目录就是那个文件的上上级。
find_existing_install() {
  local config dir
  config="$(docker compose ls --all --format json 2>/dev/null | python3 -c "
import sys, json
try:
    projects = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for project in projects:
    if project.get('Name') == '$PROJECT':
        print(project.get('ConfigFiles', '').split(',')[0])
        break
" 2>/dev/null || true)"
  [ -n "$config" ] || return 1
  dir="$(dirname "$(dirname "$config")")"
  [ -f "$dir/docker/compose.yml" ] || return 1
  printf '%s' "$dir"
}

# 运行中的实例自己报的版本。只有这个算数：磁盘上的代码可能比真正在服务的那个
# 镜像更新。
running_version() {
  "$CTL" exec -T api dtk --version 2>/dev/null | tr -d '\r' | awk 'NF{print $NF}' | tail -1
}

latest_release() {
  curl -fsSL --proto '=https' --tlsv1.2 -m 10 \
    "https://api.github.com/repos/Evil0ctal/Douyin_TikTok_Download_API/releases/latest" 2>/dev/null \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('tag_name', ''))
except Exception:
    pass
" 2>/dev/null || true
}

# $1 比 $2 新时返回真。带预发布后缀的版本比同样数字但没后缀的旧，所以
# 5.1.0.dev0 排在 5.1.0 前面 —— 和控制台里 web/src/lib/version.ts 同一套规则。
version_is_newer() {
  [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
  python3 - "$1" "$2" <<'PY' 2>/dev/null
import re, sys

def parse(raw):
    text = re.sub(r"^[vV]", "", raw.strip())
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", text)
    if not m:
        return ([0], "")
    return ([int(p or 0) for p in m.group(1).split(".")],
            re.sub(r"^[.\-_]", "", m.group(2)).strip())

a, b = parse(sys.argv[1]), parse(sys.argv[2])
width = max(len(a[0]), len(b[0]))
left = a[0] + [0] * (width - len(a[0]))
right = b[0] + [0] * (width - len(b[0]))
if left != right:
    sys.exit(0 if left > right else 1)
if a[1] == b[1]:
    sys.exit(1)
if not a[1]:
    sys.exit(0)
if not b[1]:
    sys.exit(1)
sys.exit(0 if a[1] > b[1] else 1)
PY
}

ask_choice() {
  local prompt="$1" reply
  printf '\n    %s ' "$prompt" >&2
  reply="$(read_line)"
  printf '%s' "$(printf '%s' "$reply" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
}

# 不回显地读两遍口令。调用方把它从标准输入喂给命令；绝不作为参数传 ——
# argv 对同机任何进程都可见。
read_password_twice() {
  local first second
  while true; do
    printf '    新口令（至少 8 位）：' >&2
    IFS= read -rs first <"$TTY_IN" || first=""
    printf '\n' >&2
    if [ "${#first}" -lt 8 ]; then
      warn "太短了。"
      continue
    fi
    printf '    再输一遍：' >&2
    IFS= read -rs second <"$TTY_IN" || second=""
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

  latest="$(latest_release)"
  if [ -n "$latest" ] && [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    printf '\n'
    warn "已经有 $latest 了，这个实例还在 ${installed}。"
  elif [ -n "$latest" ]; then
    printf '\n'
    ok "已经是最新发布版（${latest}）。"
  fi
}

do_upgrade() {
  local target="$1"
  step "升级到 $target"
  info "数据不受影响：命名卷不会随这次操作消失。"
  printf '\n'

  info "正在更新代码。"
  git -C "$INSTALL_DIR" fetch --quiet origin main || warn "拉取失败，就用现有的代码。"
  git -C "$INSTALL_DIR" merge --ff-only --quiet origin/main 2>/dev/null \
    || warn "本地有改动，保持原样不动。"

  # 钉住具体的 tag，而不是跟着 `latest` 跑：运维应当说得出现在跑的是哪个构建，
  # 也应当能把它换回去。
  if grep -q '^DTK_IMAGE_TAG=' "$INSTALL_DIR/.env"; then
    local tmp
    tmp="$(mktemp)" || die "Could not create a temporary file."
    TMP_FILES+=("$tmp")
    sed "s|^DTK_IMAGE_TAG=.*|DTK_IMAGE_TAG=$target|" "$INSTALL_DIR/.env" >"$tmp"
    cat "$tmp" >"$INSTALL_DIR/.env"
    rm -f "$tmp"
    ok "已把 DTK_IMAGE_TAG 钉在 $target"
  fi

  info "正在拉取。"
  "$CTL" pull api worker 2>&1 | tail -3 || die "拉取失败。旧版本还在正常运行。"
  info "正在迁移。"
  "$CTL" run --rm migrate || die "迁移失败。旧容器还在跑，什么都没有被替换。"
  info "正在重启。"
  "$CTL" up -d --wait || die "新版本没能进入健康状态。看一下：$CTL logs api"
  ok "现在是 $(running_version || echo "$target")。"
}

act_change_password() {
  step "修改口令"
  "$CTL" exec -T api dtk user list 2>/dev/null | sed 's/^/    /' || warn "读取账号列表失败。"
  printf '\n'
  local who password
  who="$(ask_value "改哪个账号？" "admin")"
  [ -n "$who" ] || return 0
  password="$(read_password_twice)"
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
  password="$(read_password_twice)"
  if printf '%s' "$password" | "$CTL" exec -T api dtk user create "$who" --role admin --stdin; then
    ok "已创建管理员 ${who}。"
  else
    warn "没成功。这个名字可能已经被占用了。"
  fi
  unset password
}

act_list_users() {
  step "账号列表"
  "$CTL" exec -T api dtk user list 2>/dev/null | sed 's/^/    /' || warn "Could not list accounts."
}

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
    "$CTL" exec -T api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' || true
  else
    warn "备份失败，请看上面的输出。"
  fi
}

act_restore() {
  step "恢复"
  # 单引号：DTK_BACKUP_DIR 要在容器里展开，不是在这里。
  # shellcheck disable=SC2016
  "$CTL" exec -T api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' \
    || { warn "没有找到备份文件。"; return 0; }
  printf '\n'
  warn "恢复会替换当前数据库。现在里面的东西会没。"
  local archive
  archive="$(ask_value "备份文件路径（照上面列的填）？" "")"
  [ -n "$archive" ] || return 0
  confirm_word "restore" || { info "已取消。"; return 0; }
  "$CTL" exec -T api dtk backup restore "$archive" --yes || warn "恢复失败，请看上面的输出。"
}

act_diagnose() {
  step "自检"
  "$CTL" exec -T api dtk diagnose 2>&1 | sed 's/^/    /' || warn "自检没能跑起来。"
}

act_logs() {
  step "日志"
  dim "每个服务最后 60 行。想实时跟踪：$CTL logs -f api"
  printf '\n'
  "$CTL" logs --tail 60 2>&1 | tail -80
}

act_restart() {
  step "重启"
  "$CTL" restart || warn "重启失败。"
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
    "$CTL" exec -T api dtk config list 2>&1 | sed 's/^/    /' | head -80
    return 0
  fi
  "$CTL" exec -T api dtk config get "$key" 2>&1 | sed 's/^/    /' || { warn "没有这个设置项。"; return 0; }
  printf '\n'
  value="$(ask_value "新的值（留空则不改）？" "")"
  [ -n "$value" ] || return 0
  "$CTL" exec -T api dtk config set "$key" "$value" 2>&1 | sed 's/^/    /' || warn "被拒绝了。上面的校验信息说明了原因。"
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

  # 「在用」= 被任何一个存在的容器引用，无论它在跑还是停着 —— 不是「和 .env 里的
  # tag 对得上」。从源码构建的安装 .env 里压根没有 tag，用那个当过滤条件时，
  # 这里会提议删掉正在服务的那个镜像。栈开着时 Docker 会拒绝，栈一停就照删不误。
  local in_use images=""
  in_use="$(docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)"
  images="$(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null \
    | grep -E '^(evil0ctal/douyin_tiktok_download_api|dtk-browser-rpc|dtk-app)' \
    | while read -r ref id; do
        printf '%s\n' "$in_use" | grep -qxF "$ref" || printf '%s %s\n' "$ref" "$id"
      done || true)"

  if [ -n "$images" ]; then
    info "本项目中、当前没有被任何容器使用的镜像："
    printf '%s\n' "$images" | sed 's/^/      /'
    printf '\n'
    if ask_yes_no "删掉它们吗？" "y"; then
      printf '%s\n' "$images" | awk '{print $2}' | sort -u | while read -r id; do
        docker rmi "$id" >/dev/null 2>&1 && info "已删除 $id" || true
      done
    fi
  else
    ok "本项目没有可清理的陈旧镜像。"
  fi

  printf '\n'
  dim "构建缓存能极大加快浏览器镜像的重建 —— 小改动后重建只要几秒，全靠它。"
  dim "清掉就没这个便宜了。"
  if ask_yes_no "顺便清掉构建缓存吗？" "n"; then
    docker builder prune -af 2>&1 | tail -2 | sed 's/^/    /'
  fi

  printf '\n'
  info "清理后："
  docker system df 2>/dev/null | sed 's/^/      /'
}

# ------------------------------------------------------------------ 菜单 --

menu_uninstall() {
  while true; do
    step "停止或移除"
    info "  1  停掉服务，数据一个字节都不动"
    info "  2  停掉并删除数据卷 —— 数据库、媒体、备份"
    info "  3  在 2 的基础上，连 $INSTALL_DIR 一起删掉"
    info "  b  返回"
    case "$(ask_choice "选哪个？")" in
      1) "$CTL" down --remove-orphans && ok "已停止。要再启动：$CTL up -d" ; return 0 ;;
      2)
        warn "这会删掉数据库、媒体卷，以及里面的每一份备份。"
        confirm_word "delete" || { info "已取消。"; continue; }
        "$CTL" down -v --remove-orphans && ok "已停止，数据卷已删除。"
        return 0 ;;
      3)
        warn "这会删掉数据卷，还会删掉 ${INSTALL_DIR}，包括你的 .env。"
        warn "反正没有那个 .env，数据本来也解不开。"
        confirm_word "delete" || { info "已取消。"; continue; }
        "$CTL" down -v --remove-orphans || true
        # 不信任变量，而是加护栏：这是整个脚本唯一一处删目录树的地方，
        # 空值或者离谱的值绝不能走到 rm 那一步。
        case "$INSTALL_DIR" in
          ""|"/"|"/usr"|"/etc"|"/var"|"/home"|"/root"|"/opt")
            die "拒绝删除 ${INSTALL_DIR}。" ;;
        esac
        [ -f "$INSTALL_DIR/docker/compose.yml" ] || die "$INSTALL_DIR 看着不像一份安装，不删。"
        rm -rf -- "$INSTALL_DIR" && ok "已删除 ${INSTALL_DIR}。"
        return 0 ;;
      b|"") return 0 ;;
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
    if [ -n "$upgrade_line" ]; then
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
        if [ -n "$upgrade_line" ]; then
          do_upgrade "$upgrade_line"
        else
          local tag
          tag="$(grep '^DTK_IMAGE_TAG=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || echo latest)"
          do_upgrade "${tag:-latest}"
        fi
        pause_for_reader ;;
      3) menu_manage ;;
      4) menu_uninstall; return 0 ;;
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
                 不建浏览器容器。
  --check        只探测系统并打印将会发生什么，然后停下。不改任何东西。
  --manage       直接进入已有安装的管理菜单。
  --help, -h     这段说明。

不带任何选项运行时它会自己判断该做哪件事：没装过就安装，装过就打开菜单 ——
查看状态、升级、改口令、备份、改设置、清磁盘。

环境变量（每一个都能预设一个答案，配合 --yes 就是无人值守安装）：
  DTK_PROJECT            Compose 项目名。默认：dtk。
  DTK_INSTALL_DIR        装到哪里。默认：root 是 /opt/dtk，否则 ~/dtk。
  DTK_BIND_HOST          127.0.0.1（默认）或 0.0.0.0。
  DTK_BIND_PORT          默认：8000。
  DTK_ENABLE_BROWSER     1 构建浏览器容器，0（默认）不建。
  DTK_ENABLE_DOWNLOADER  1（默认）启用媒体下载，0 不启用。
  DTK_USE_PUBLISHED      1（默认）拉已发布镜像，0 从源码构建。
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)   ASSUME_YES=1 ;;
      --check)    CHECK_ONLY=1 ;;
      --manage)   MANAGE_ONLY=1 ;;
      -h|--help)  usage; exit 0 ;;
      *)          die "无法识别的选项：$1（试试 --help）" ;;
    esac
    shift
  done

  banner
  detect_os
  detect_resources
  report_host

  step "检查 Docker"
  if ! check_docker_present; then
    if [ "$CHECK_ONLY" = 1 ]; then
      warn "没有装 Docker。正式运行时脚本会问你要不要装。"
      info "这个系统对应的包管理器命令："
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

  # 已经装过的话，这个脚本就从"安装器"变成"管理工具"。位置是问 Docker 要的，
  # 所以不管当初装在哪都能找到 —— 不只是这个脚本会建议的那两个目录。
  local found=""
  if [ -z "${DTK_INSTALL_DIR:-}" ] && [ "$ASSUME_YES" = 0 ]; then
    found="$(find_existing_install || true)"
  elif [ "$MANAGE_ONLY" = 1 ]; then
    found="${DTK_INSTALL_DIR:-$(find_existing_install || true)}"
  fi
  if [ "$MANAGE_ONLY" = 1 ] && { [ -z "$found" ] || [ ! -x "$found/dtkctl" ]; }; then
    die "没有找到项目 '${PROJECT}' 的安装。
    去掉 --manage 可以新建一个；或者用 DTK_INSTALL_DIR 指定它在哪。"
  fi
  if [ -n "$found" ] && [ -x "$found/dtkctl" ]; then
    INSTALL_DIR="$found"
    CTL="$found/dtkctl"
    if [ -z "$TTY_IN" ]; then
      warn "${INSTALL_DIR} 已经有一份安装了。"
      info "想管理它请在终端里跑这个脚本，或者直接用 ${CTL}。"
      exit 0
    fi
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
  prepare_directory
  ask_questions
  write_env
  write_host_override
  write_control_script
  CTL="${INSTALL_DIR}/dtkctl"
  bring_up
  show_result
}

main "$@"
