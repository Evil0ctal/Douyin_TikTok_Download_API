#!/usr/bin/env bash
#
# Douyin_TikTok_Download_API v5 — guided Docker install.
#
#   curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh -o install.sh
#   less install.sh          # read it first; you are about to run it
#   bash install.sh
#
# Docker only. Installing without Docker means putting PostgreSQL with the
# TimescaleDB extension, Redis, Python and Node on the host yourself, which is a
# document rather than a script: documents/en/02-installation.md.
#
# What this script will and will not do
# -------------------------------------
# It writes only inside the directory you choose, plus Docker's own volumes.
# It never edits a file outside that directory, never adds a cron job, never
# opens a firewall port, and never installs anything without asking first.
#
# It uses sudo only for: installing Docker (if you say yes), starting the Docker
# service, and creating the install directory when that directory needs root.
# Each of those asks first and prints the exact command.
#
# The Chinese version of this script is install.zh.sh in the same directory.
# The two are kept structurally identical; only the messages differ.

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------- constants --

readonly REPO_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git"
readonly REPO_RAW="https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main"
readonly DOCKER_INSTALL_URL="https://get.docker.com"
readonly DOCS_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/02-installation.md"

# Compose gained the long `env_file:` form and COMPOSE_ENV_FILES in 2.24, and
# docker/compose.yml uses both. Anything older fails in ways that read like a
# problem with this project.
readonly COMPOSE_MIN="2.24"

# The three FunASR models the transcriber loads, pinned to the commits that
# documents/en/18-funasr-models.md verified, and the SHA-256 of every file the
# sidecar reads. A commit cannot move under a download the way `master` can,
# and the hashes turn a truncated or substituted file into an error here rather
# than a sidecar that never becomes healthy.
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

# This file's name inside an install's checkout. dtkctl runs that copy to
# upgrade, and an upgrade hands over to it once the checkout is new. The
# Chinese script names itself, so an install made in Chinese stays in Chinese.
readonly SCRIPT_NAME="install.sh"

# The CloakBrowser revision the browser image installs - the same one
# .env.example carries, verified end to end with this repository. It is a
# build argument, and dtkctl points Compose interpolation at the root .env, so
# that is where it goes. Before it was, every browser install this script made
# built an image with no browser in it; its healthcheck reads that, so the
# install died at `up --wait` with browser-rpc unhealthy.
readonly CLOAKBROWSER_PIN="fbf005bc199920b592df17ac4d489324e29d56d8"

# Overridable for testing, so a trial run cannot touch a real `dtk` stack.
# Validated because it is written into the generated dtkctl: anything that is
# not a plain name would end up as shell syntax there.
PROJECT="${DTK_PROJECT:-dtk}"
case "$PROJECT" in
  ''|*[!a-zA-Z0-9_-]*)
    printf 'error: DTK_PROJECT must be letters, digits, dash or underscore.\n' >&2
    exit 1 ;;
esac

# ------------------------------------------------------------------ output --

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
die()  { printf '\n%serror:%s %s\n\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ------------------------------------------------------------------- input --
#
# Reading input when the script is piped is the thing that breaks naive
# installers: with `curl … | bash`, stdin IS the script, so `read` swallows the
# script's own remaining lines. So input always comes from the terminal
# explicitly, and if there is no terminal the script says so instead of
# silently taking defaults for questions like "which address do I publish on".

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

# Anything we create outside the install directory is registered here, so an
# interrupt does not leave it behind.
TMP_FILES=()
# It has to end in success. A script that runs off its end exits with the
# status of the last command it ran, which is this trap; with nothing
# registered, `[ -n "" ]` was that command, and every install that needed no
# temporary file reported failure after printing "Done".
cleanup() {
  local f
  for f in "${TMP_FILES[@]:-}"; do
    if [ -n "$f" ]; then rm -f "$f"; fi
  done
  return 0
}
trap cleanup EXIT INT TERM

# Read one line from the terminal. Returns empty on EOF rather than failing the
# script, so a closed terminal falls through to the default.
read_line() {
  local reply=""
  if [ -n "$TTY_IN" ]; then
    IFS= read -r reply <"$TTY_IN" || reply=""
  fi
  printf '%s' "$reply"
}

# ask_yes_no "question" "y|n"  -> returns 0 for yes, 1 for no
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
      *)         warn "Please answer y or n." ;;
    esac
  done
}

# ask_value "question" "default" [validator-function]
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
    ''|*[!0-9]*) warn "A port is a number."; return 1 ;;
  esac
  if [ "$1" -lt 1 ] || [ "$1" -gt 65535 ]; then
    warn "A port is between 1 and 65535."
    return 1
  fi
  return 0
}

valid_path() {
  case "$1" in
    /|"") warn "That is not a directory this should install into."; return 1 ;;
    /*|~*|./*|../*) return 0 ;;
    *) warn "Use an absolute path, for example /opt/dtk."; return 1 ;;
  esac
}

# Absolute, with no trailing slash and no '..' left in it. The refusal list
# below compares against literal names, so it only means anything once the path
# it is comparing is the real one.
absolute_path() {
  local path="$1"
  case "$path" in
    /*) : ;;
    *)  path="$PWD/$path" ;;
  esac
  # Resolve without requiring the directory to exist yet, which realpath -e
  # would.
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

# Run a command with sudo when we are not root, printing it first. Never used
# on a whole block - only on the single command that genuinely needs it.
as_root() {
  if [ "$(id -u)" = "0" ]; then
    "$@"
  else
    dim "sudo $*"
    sudo "$@"
  fi
}

# --------------------------------------------------------------- detection --

OS=""          # linux | macos | wsl | unsupported
DISTRO=""      # debian, ubuntu, fedora, rhel, arch, alpine, opensuse …
DISTRO_LIKE="" # ID_LIKE from os-release
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
      # WSL is Linux, but Docker there is Docker Desktop on the Windows side and
      # a package-manager install is the wrong advice.
      if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then OS="wsl"; fi
      if [ -r /etc/os-release ]; then
        # Sourced in a subshell so os-release's variables cannot leak into
        # this script's namespace - it sets things like NAME and VERSION.
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

# Which family a distribution belongs to, for the Docker install advice.
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

# ------------------------------------------------------------------ docker --

# "2.29.7" -> 2029007, so versions compare as integers without sort -V.
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
      warn "Compose is $raw; this stack needs $COMPOSE_MIN or newer."
      info "Update Docker, then run this again. See $DOCS_URL"
      return 1
    fi
    ok "Docker Compose $raw"
    return 0
  fi
  if have docker-compose; then
    warn "Found the old standalone docker-compose (v1)."
    info "This stack uses Compose v2 features. Install a current Docker, which"
    info "ships Compose as a plugin: $DOCKER_INSTALL_URL"
    return 1
  fi
  warn "Docker Compose v2 is not installed."
  return 1
}

docker_install_hint() {
  local family
  family="$(distro_family)"
  case "$family" in
    debian) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2" ;;
    rhel)   printf '%s' "sudo dnf install -y docker docker-compose-plugin  # or: yum" ;;
    suse)   printf '%s' "sudo zypper install -y docker docker-compose" ;;
    arch)   printf '%s' "sudo pacman -S --needed docker docker-compose" ;;
    alpine) printf '%s' "sudo apk add docker docker-cli-compose && sudo rc-update add docker default" ;;
    nixos)  printf '%s' "add virtualisation.docker.enable = true; to configuration.nix" ;;
    void)   printf '%s' "sudo xbps-install -S docker docker-compose" ;;
    gentoo) printf '%s' "sudo emerge app-containers/docker app-containers/docker-compose" ;;
    *)      printf '%s' "see https://docs.docker.com/engine/install/" ;;
  esac
}

git_install_hint() {
  case "$(distro_family)" in
    debian) printf '%s' "sudo apt-get install -y git" ;;
    rhel)   printf '%s' "sudo dnf install -y git" ;;
    suse)   printf '%s' "sudo zypper install -y git" ;;
    arch)   printf '%s' "sudo pacman -S --needed git" ;;
    alpine) printf '%s' "sudo apk add git" ;;
    *)      printf '%s' "your package manager" ;;
  esac
}

# Whether the official convenience script supports this distribution. It covers
# the Debian, RHEL and SUSE families; on Arch and Alpine it refuses, and telling
# somebody to pipe a script that will refuse them is worse than telling them the
# one command their package manager wants.
convenience_script_supported() {
  case "$(distro_family)" in
    debian|rhel|suse) return 0 ;;
    *) return 1 ;;
  esac
}

offer_docker_install() {
  case "$OS" in
    macos)
      die "Docker is not installed.
    On macOS install Docker Desktop or OrbStack, start it, then run this again:
      https://www.docker.com/products/docker-desktop/
    Homebrew: brew install --cask docker"
      ;;
    wsl)
      die "Docker is not available inside this WSL distribution.
    Install Docker Desktop on Windows and turn on WSL integration for this
    distribution, then run this again:
      https://docs.docker.com/desktop/wsl/"
      ;;
    unsupported)
      die "This script installs Docker only on Linux and expects Docker Desktop
    on macOS. On $DISTRO_NAME, install Docker yourself and run this again:
      https://docs.docker.com/engine/install/"
      ;;
  esac

  warn "Docker is not installed."
  info ""
  info "Two ways to fix that. Both need sudo."
  info ""
  info "  1. Your package manager:"
  dim  "     $(docker_install_hint)"
  if convenience_script_supported; then
    info ""
    info "  2. Docker's own installer, which this script can run for you:"
    dim  "     curl -fsSL $DOCKER_INSTALL_URL | sudo sh"
    info ""
    info "     That downloads a shell script from Docker Inc and runs it as root."
    info "     It is the official one and it is what Docker's own documentation"
    info "     tells you to use, but it is still remote code as root, so it is"
    info "     your call rather than the default."
    info ""
    if ask_yes_no "Run Docker's installer now?" "n"; then
      step "Installing Docker"
      local tmp
      tmp="$(mktemp -t dtk-docker-install.XXXXXX)" || die "Could not create a temporary file."
      TMP_FILES+=("$tmp")
      # Download first, then run: a pipe straight into a shell cannot be
      # inspected, and a truncated download becomes a half-executed script.
      if ! curl -fsSL --proto '=https' --tlsv1.2 "$DOCKER_INSTALL_URL" -o "$tmp"; then
        rm -f "$tmp"
        die "Could not download the Docker installer. Install Docker by hand and run this again."
      fi
      if [ ! -s "$tmp" ]; then
        rm -f "$tmp"
        die "The Docker installer downloaded empty. Install Docker by hand and run this again."
      fi
      info "Downloaded to $tmp ($(wc -c <"$tmp" | tr -d ' ') bytes)."
      as_root sh "$tmp" || { rm -f "$tmp"; die "The Docker installer failed. Read its output above."; }
      rm -f "$tmp"
      ok "Docker installed."
    else
      die "Install Docker with the command above, then run this script again."
    fi
  else
    info ""
    info "  2. Docker's own installer does not support $DISTRO_NAME, so use the"
    info "     package manager command above."
    die "Install Docker, then run this script again."
  fi
}

ensure_docker_running() {
  if check_docker_running; then
    return 0
  fi

  if [ "$OS" = "macos" ]; then
    die "Docker is installed but not running. Start Docker Desktop (or OrbStack)
    and wait for it to say it is running, then run this script again."
  fi

  warn "Docker is installed but not answering."
  if have systemctl && ask_yes_no "Start the Docker service now?" "y"; then
    as_root systemctl enable --now docker || true
    sleep 2
  elif have rc-service && ask_yes_no "Start the Docker service now?" "y"; then
    as_root rc-update add docker default || true
    as_root rc-service docker start || true
    sleep 2
  fi

  if check_docker_running; then
    ok "Docker is running."
    return 0
  fi

  # The other common cause: the daemon is up but this user is not allowed to
  # talk to it. Say which of the two it is rather than "cannot connect".
  if [ "$(id -u)" != "0" ] && sudo -n docker info >/dev/null 2>&1; then
    die "Docker is running, but your user cannot reach it.
    Add yourself to the docker group and start a new login shell:
      sudo usermod -aG docker \"\$USER\"
      newgrp docker
    Then run this script again. (Group membership is the same as root access on
    this machine, which is why this script will not do it for you.)"
  fi

  die "Docker is installed but not running, and starting it did not work.
    Start it however this system does, check with 'docker info', then run this
    script again."
}

# ------------------------------------------------------------------ random --

random_hex() {
  local bytes="$1"
  if have openssl; then
    openssl rand -hex "$bytes"
  elif [ -r /dev/urandom ] && have od; then
    od -An -tx1 -N "$bytes" /dev/urandom | tr -d ' \n'
  else
    die "No source of randomness (openssl or /dev/urandom). Cannot generate secrets."
  fi
}

random_b64() {
  local bytes="$1"
  if have openssl; then
    # base64 can contain / and +, which is fine for DTK_SECRET_KEY: it is not
    # put in a URL. The two database passwords use hex for exactly that reason.
    openssl rand -base64 "$bytes" | tr -d '\n'
  elif [ -r /dev/urandom ] && have base64; then
    head -c "$bytes" /dev/urandom | base64 | tr -d '\n'
  else
    die "No source of randomness (openssl or /dev/urandom). Cannot generate secrets."
  fi
}

# --------------------------------------------------------------- .env, dtkctl --
#
# The installer writes .env and dtkctl whole. Everything after it - turning
# transcription on from the menu, or off when a step fails - changes one line
# and leaves the rest of each file exactly as somebody may have edited it.

env_value() {
  grep "^$1=" "$INSTALL_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

# Replace KEY's line, or append it. Through a temporary file and `cat >`
# rather than `mv`, so .env keeps its inode, its owner and its 0600 mode.
set_env_value() {
  local key="$1" value="$2" env_file="$INSTALL_DIR/.env" tmp
  tmp="$(mktemp)" || die "Could not create a temporary file."
  TMP_FILES+=("$tmp")
  if grep -q "^${key}=" "$env_file"; then
    awk -v key="$key" -v value="$value" \
      'index($0, key "=") == 1 { print key "=" value; next } { print }' "$env_file" >"$tmp"
  else
    cat "$env_file" >"$tmp"
    # A file that does not end in a newline would glue the new key onto its
    # last value.
    if [ -n "$(tail -c 1 "$env_file")" ]; then printf '\n' >>"$tmp"; fi
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  cat "$tmp" >"$env_file"
  rm -f "$tmp"
}

# Add or remove one --profile flag on dtkctl's compose line, in place.
set_ctl_profile() {
  local name="$1" state="$2" flag tmp
  flag=" --profile $name"
  tmp="$(mktemp)" || die "Could not create a temporary file."
  TMP_FILES+=("$tmp")
  if [ "$state" = on ]; then
    # Single quotes: "$@" is awk text that ends up in dtkctl, not an expansion.
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
    warn "Could not add$flag to $CTL. Add it to the compose line by hand."
  fi
}

# ----------------------------------------------------------- transcription --
#
# The transcriber is the one optional component whose data is not in an image:
# three models, about 500 MB, that the container mounts read-only and never
# fetches. What follows gets them onto this machine and proves they are the
# right bytes.

sha256_of() {
  if have sha256sum; then
    sha256sum "$1" | awk '{print $1}'
  elif have shasum; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    return 1
  fi
}

# Every file the sidecar reads, there and bigger than a Git LFS pointer. Not
# the hashes: a directory somebody prepared by hand may hold a newer upstream
# revision, and the sidecar's own start-up check decides whether that loads.
models_present() {
  local root="$1" entry path size
  for entry in "${MODEL_FILES[@]}"; do
    path="${entry%%|*}"
    [ -s "$root/$path" ] || return 1
    case "$path" in
      *.onnx)
        # A pointer is about 130 bytes; the smallest of these models is 500 KB.
        size="$(wc -c <"$root/$path" | tr -d ' ')"
        [ "$size" -gt 100000 ] || return 1
        ;;
    esac
  done
  return 0
}

# The container reads the models as UID 10001, not as whoever ran this. A
# directory open only to its owner starts a transcriber that never turns
# healthy, and the reason is only in that container's log.
models_readable() {
  [ -z "$(find "$1" \( -type d ! -perm -005 \) -o \( -type f ! -perm -004 \) 2>/dev/null | head -1)" ]
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

# File by file from the pinned commits. A file already there with the right
# hash is kept, so a second attempt after a dropped connection fetches only
# what the first one did not finish.
download_models() {
  local root="$1" entry path want origin repo commit tmp got progress="-sS"
  have curl || { warn "Downloading the models needs curl, which is not installed."; return 1; }
  if ! sha256_of /dev/null >/dev/null 2>&1; then
    warn "Neither sha256sum nor shasum is installed, so the models cannot be checked."
    return 1
  fi
  if [ -t 2 ]; then progress="--progress-bar"; fi

  step "Downloading the transcription models"
  info "About 500 MB from ModelScope, at the commits the documentation verified."
  info "Each file is checked against its SHA-256 before it is kept."
  mkdir -p "$root/asr" "$root/vad" "$root/punc" || { warn "Could not create $root."; return 1; }

  for entry in "${MODEL_FILES[@]}"; do
    path="${entry%%|*}"
    want="${entry##*|}"
    if [ -f "$root/$path" ] && [ "$(sha256_of "$root/$path")" = "$want" ]; then
      ok "$path (already here)"
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
      warn "Could not download $path."
      return 1
    fi
    got="$(sha256_of "$tmp")"
    if [ "$got" != "$want" ]; then
      rm -f "$tmp"
      warn "$path does not match its checksum, so it was not kept."
      warn "Expected $want, got $got."
      return 1
    fi
    mv -f "$tmp" "$root/$path"
  done
  chmod -R a+rX "$root"
  ok "All ten files verified in $root"
}

# A full commit and nothing else. A tag or a branch would move under the pin,
# and anything that is not hex ends up as a build argument Docker rejects
# several minutes into the build.
valid_commit() {
  case "$1" in
    *[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ]
}

# Installs written before the pin existed have none, and their browser image
# holds no browser. Filled in rather than overwritten: a pin somebody chose
# deliberately stays theirs.
ensure_browser_pin() {
  if [ -z "$(env_value CLOAKBROWSER_COMMIT)" ]; then
    set_env_value CLOAKBROWSER_COMMIT "$BROWSER_COMMIT"
    ok "Pinned CloakBrowser to $BROWSER_COMMIT in .env."
  fi
}

# Compose reads this path out of .env into a short volume spec, where a colon
# ends the path and a space or a '#' changes what the line means first.
valid_model_path() {
  valid_path "$1" || return 1
  case "$1" in
    *[!A-Za-z0-9._/~+-]*) warn "Use letters, digits and . _ - + / only in this path."; return 1 ;;
  esac
  return 0
}

# Where the models are, or where they should go. A directory that already
# holds them is used as it is; anything else is offered a download into it.
choose_model_path() {
  MODELS_TO_FETCH=0
  dim "The models stay on this machine and are mounted read-only. A directory"
  dim "that already holds them is used as it is."
  MODEL_PATH="$(ask_value "Model directory?" "${MODEL_PATH:-$INSTALL_DIR/models/funasr-onnx}" valid_model_path)"
  # An answer is validated as it is typed; a preset taken by --yes is not.
  if ! valid_model_path "$MODEL_PATH"; then
    WANT_TRANSCRIBER=0
    return 0
  fi
  # The tilde arrives literal from `read`, as in choose_directory.
  # shellcheck disable=SC2088
  case "$MODEL_PATH" in
    "~") MODEL_PATH="$HOME" ;;
    "~/"*) MODEL_PATH="$HOME/${MODEL_PATH#\~/}" ;;
  esac
  MODEL_PATH="$(absolute_path "$MODEL_PATH")"

  if [ -e "$MODEL_PATH" ] && [ ! -d "$MODEL_PATH" ]; then
    warn "$MODEL_PATH exists and is not a directory. Transcription stays off."
    WANT_TRANSCRIBER=0
    return 0
  fi
  if models_present "$MODEL_PATH"; then
    ok "Found the models in $MODEL_PATH."
    if ! models_readable "$MODEL_PATH"; then
      warn "Some of them are closed to other users, and the container reads them as UID 10001."
      if ask_yes_no "Make them readable (chmod -R a+rX; nothing becomes writable)?" "y"; then
        chmod -R a+rX "$MODEL_PATH" || { warn "chmod failed. Transcription stays off."; WANT_TRANSCRIBER=0; }
      else
        WANT_TRANSCRIBER=0
        dim "Transcription stays off until they are readable."
      fi
    fi
    return 0
  fi

  dim "There is no complete set of models there yet."
  if ask_yes_no "Download them now (about 500 MB, each file checked against its SHA-256)?" "y"; then
    MODELS_TO_FETCH=1
  else
    WANT_TRANSCRIBER=0
    dim "Transcription stays off. Prepare the models by hand - documents/en/18-funasr-models.md -"
    dim "and turn it on from the menu afterwards."
  fi
}

# The transcriber's lines in an .env that already exists. An existing token is
# kept: nothing gains from a new secret, and both sides read whichever is there.
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

# An optional component that cannot be set up does not fail the install: the
# rest of the stack is worth having without it, and the menu turns it on later.
transcription_off() {
  WANT_TRANSCRIBER=0
  set_env_value DTK_TRANSCRIBER_URL ""
  set_ctl_profile transcriber off
  warn "$1"
  info "Transcription is off; everything else carries on. Turn it on later from"
  info "the menu: bash install.sh --manage, then Manage, 11."
}

# ------------------------------------------------------------------- steps --

# Every answer can be preset from the environment, which is what makes --yes a
# complete unattended install rather than just "all defaults". Anything not set
# falls back to the value that is safe to pick for somebody who is not here.
INSTALL_DIR=""
BIND_HOST="${DTK_BIND_HOST:-127.0.0.1}"
BIND_PORT="${DTK_BIND_PORT:-8000}"
WANT_BROWSER="${DTK_ENABLE_BROWSER:-0}"
WANT_DOWNLOADER="${DTK_ENABLE_DOWNLOADER:-1}"
USE_PUBLISHED="${DTK_USE_PUBLISHED:-1}"
WANT_TRANSCRIBER="${DTK_ENABLE_TRANSCRIBER:-0}"
MODEL_PATH="${DTK_TRANSCRIBER_MODEL_PATH:-}"
MODELS_TO_FETCH=0
BROWSER_COMMIT="${CLOAKBROWSER_COMMIT:-$CLOAKBROWSER_PIN}"

banner() {
  printf '\n'
  printf '%s  Douyin_TikTok_Download_API v5 · guided install%s\n' "$C_BOLD" "$C_RESET"
  printf '%s  Docker only. Nothing is installed or changed without asking.%s\n' "$C_DIM" "$C_RESET"
  printf '\n'
}

report_host() {
  step "Looking at this machine"
  ok "$DISTRO_NAME ($ARCH)"
  ok "$CPUS CPU $( [ "$CPUS" = 1 ] && echo core || echo cores ), $(( MEM_MIB / 1024 )).$(( (MEM_MIB % 1024) * 10 / 1024 )) GiB RAM"

  case "$ARCH" in
    x86_64|arm64) : ;;
    *) die "Images are published for x86_64 and arm64 only; this is $ARCH.
    You can still build from source: $DOCS_URL" ;;
  esac

  if [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 1900 ]; then
    warn "Under 2 GiB of RAM. The core stack idles at about 300 MiB, but"
    warn "PostgreSQL will be unhappy and the browser container is out of reach."
  fi
}

choose_directory() {
  step "Where to install"
  local default_dir
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    default_dir="$DTK_INSTALL_DIR"
  elif [ "$(id -u)" = "0" ]; then
    default_dir="/opt/dtk"
  else
    default_dir="$HOME/dtk"
  fi
  dim "The checkout, your .env and the control script live here. Media and the"
  dim "database live in Docker volumes, not here."
  INSTALL_DIR="$(ask_value "Install directory?" "$default_dir" valid_path)"

  # Expand a leading ~ ourselves; it arrives literal from `read`.
  # The tilde is a literal here: `read` does not expand it, so it arrives as a
  # character and this is a pattern match, not an attempt at expansion.
  # shellcheck disable=SC2088
  case "$INSTALL_DIR" in
    "~") INSTALL_DIR="$HOME" ;;
    "~/"*) INSTALL_DIR="$HOME/${INSTALL_DIR#\~/}" ;;
  esac
  INSTALL_DIR="$(absolute_path "$INSTALL_DIR")"

  case "$INSTALL_DIR" in
    ""|"/"|"/usr"|"/etc"|"/var"|"/bin"|"/sbin"|"/lib"|"/boot"|"/home"|"/root")
      die "Refusing to install into $INSTALL_DIR." ;;
  esac
}

prepare_directory() {
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR exists and is not a directory."
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    local origin
    origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "")"
    case "$origin" in
      *Douyin_TikTok_Download_API*)
        ok "Existing install found at $INSTALL_DIR."
        info "Your .env and your Docker volumes are left alone."
        if ask_yes_no "Update the checkout to the latest main?" "y"; then
          git -C "$INSTALL_DIR" fetch --quiet origin main
          git -C "$INSTALL_DIR" checkout --quiet main 2>/dev/null || true
          # --ff-only, never --hard: a reset would throw away edits somebody
          # made on purpose, and this script has no business doing that.
          git -C "$INSTALL_DIR" merge --ff-only --quiet origin/main \
            || warn "The checkout has local changes; leaving it as it is."
        fi
        return
        ;;
      *)
        die "$INSTALL_DIR is a git checkout of something else ($origin).
    Choose an empty directory." ;;
    esac
  fi

  if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    die "$INSTALL_DIR is not empty and is not an existing install. Choose another directory."
  fi

  step "Fetching the source"
  have git || die "git is not installed. Install it with $(git_install_hint), then run this again."

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
    || die "Could not clone $REPO_URL. Check the network, or a mirror if you are in mainland China: $DOCS_URL"
  ok "Cloned into $INSTALL_DIR"
}

ask_questions() {
  step "A few questions"

  dim "Publishing on 127.0.0.1 means only this machine can reach the console."
  dim "Put a reverse proxy with TLS in front to expose it; 0.0.0.0 puts an"
  dim "unencrypted admin console straight onto the network."
  local loopback_default="y"
  [ "$BIND_HOST" = "0.0.0.0" ] && loopback_default="n"
  if ask_yes_no "Keep it on 127.0.0.1 (recommended)?" "$loopback_default"; then
    BIND_HOST="127.0.0.1"
  else
    BIND_HOST="0.0.0.0"
    warn "Publishing on 0.0.0.0. Put TLS in front of it before anyone else can route to this host."
  fi
  BIND_PORT="$(ask_value "Which port?" "$BIND_PORT" valid_port)"

  printf '\n'
  dim "The browser container mints guest identities by itself, so you never"
  dim "paste cookies. It is also the expensive part: about 4 GiB of RAM and a"
  dim "6 GB image, and it is built on this machine rather than pulled."
  local browser_default="y"
  [ "${DTK_ENABLE_BROWSER:-}" = "0" ] && browser_default="n"
  [ "${DTK_ENABLE_BROWSER:-}" = "1" ] && browser_default="y"
  # Unattended runs never build it. It is a multi-minute build of a 6 GB image
  # that reaches GitHub and a browser CDN, which is not something to start
  # because somebody passed --yes.
  if [ "$ASSUME_YES" = 1 ]; then
    browser_default="n"
  elif [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 6000 ]; then
    browser_default="n"
    dim "This machine has under 6 GiB, so the suggested answer is no. You can"
    dim "still import your own cookies from the console and use everything else."
  fi
  # An explicit DTK_ENABLE_BROWSER=1 outranks the "not unattended" rule: it is
  # somebody saying so, not a default being taken on their behalf.
  [ "${DTK_ENABLE_BROWSER:-}" = "1" ] && browser_default="y"
  if ask_yes_no "Enable the browser container?" "$browser_default"; then WANT_BROWSER=1; else WANT_BROWSER=0; fi
  if [ "$WANT_BROWSER" = 1 ]; then
    valid_commit "$BROWSER_COMMIT" \
      || die "CLOAKBROWSER_COMMIT has to be a full 40-character commit, not '$BROWSER_COMMIT'."
    dim "It installs CloakBrowser at commit ${BROWSER_COMMIT:0:12}, the revision this"
    dim "script was verified with. CLOAKBROWSER_COMMIT in .env changes it."
  fi

  printf '\n'
  dim "The downloader saves media to a Docker volume on this host. Small and"
  dim "cheap; without it, download requests answer 501 and say why."
  local dl_default="y"; [ "$WANT_DOWNLOADER" = 0 ] && dl_default="n"
  if ask_yes_no "Enable media downloads?" "$dl_default"; then WANT_DOWNLOADER=1; else WANT_DOWNLOADER=0; fi

  printf '\n'
  dim "Offline transcription turns stored videos into text on this machine, with"
  dim "FunASR models trained for Mandarin. About 1 GiB of RAM and two cores while"
  dim "it works, and 500 MB of models that this can download and check for you."
  # Off unless asked for, like the browser: it is a download from a third
  # party and a second CPU-hungry container, not something to start because
  # somebody passed --yes.
  local tx_default="n"
  [ "${DTK_ENABLE_TRANSCRIBER:-}" = "1" ] && tx_default="y"
  if [ "$WANT_DOWNLOADER" = 0 ]; then
    dim "It transcribes the videos the downloader stores, so with media downloads"
    dim "off it stays off."
    WANT_TRANSCRIBER=0
  elif [ "$CPUS" -lt 2 ]; then
    dim "It needs two cores and this machine has one, so it stays off."
    WANT_TRANSCRIBER=0
  else
    if ask_yes_no "Enable offline transcription?" "$tx_default"; then WANT_TRANSCRIBER=1; else WANT_TRANSCRIBER=0; fi
    if [ "$WANT_TRANSCRIBER" = 1 ] && [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 4000 ]; then
      warn "Under 4 GiB of RAM. It fits, but give this machine swap."
    fi
  fi
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    choose_model_path
  fi

  printf '\n'
  dim "Published images are pulled and start in about a minute. Building from"
  dim "source takes several minutes and needs npm and uv to reach the network."
  local pub_default="y"; [ "$USE_PUBLISHED" = 0 ] && pub_default="n"
  if ask_yes_no "Use the published images (recommended)?" "$pub_default"; then USE_PUBLISHED=1; else USE_PUBLISHED=0; fi

}

# Before .env and dtkctl exist, so a download that fails leaves an install with
# transcription off rather than one pointing at models that are not there.
fetch_models() {
  if [ "$WANT_TRANSCRIBER" = 0 ] || [ "$MODELS_TO_FETCH" = 0 ]; then
    return 0
  fi
  if ! download_models "$MODEL_PATH"; then
    WANT_TRANSCRIBER=0
    warn "Transcription stays off. What did download is kept, so the next attempt"
    warn "fetches only the rest."
  fi
}

write_env() {
  step "Writing .env"
  local env_file="$INSTALL_DIR/.env"

  if [ -f "$env_file" ]; then
    ok "Keeping the existing .env — its secrets are what the database was built with."
    info "Delete it yourself if you want a fresh one, but the old volumes will not open."
    # dtkctl is about to start the transcriber, and an .env from before it
    # existed would leave the api without its address and it without a token.
    if [ "$WANT_TRANSCRIBER" = 1 ]; then
      write_transcriber_env
    fi
    if [ "$WANT_BROWSER" = 1 ]; then
      ensure_browser_pin
    fi
    return
  fi

  local pg_pass redis_pass secret dl_token tx_token
  pg_pass="$(random_hex 24)"
  redis_pass="$(random_hex 24)"
  secret="$(random_b64 48)"
  dl_token="$(random_hex 24)"
  tx_token="$(random_hex 24)"

  # 0600 from the moment it exists: this file is the instance's keys.
  local old_umask
  old_umask="$(umask)"
  umask 077
  {
    printf '# Written by install/install.sh on %s.\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')"
    printf '# Secrets in here are what your database and cookie jars are encrypted with.\n'
    printf '# Back it up somewhere safe. Losing it means losing the stored credentials.\n\n'
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
    # Written whether or not the browser is on, so that turning it on later
    # by hand builds an image with a browser in it.
    printf 'CLOAKBROWSER_COMMIT=%s\n' "$BROWSER_COMMIT"
    if [ "$USE_PUBLISHED" = 1 ]; then
      printf 'DTK_IMAGE=evil0ctal/douyin_tiktok_download_api\n'
      printf 'DTK_IMAGE_TAG=latest\n'
      printf 'DTK_DOWNLOADER_IMAGE=evil0ctal/douyin_tiktok_download_api-downloader\n'
      printf 'DTK_TRANSCRIBER_IMAGE=%s\n' "$TRANSCRIBER_IMAGE"
    fi
  } >"$env_file"
  umask "$old_umask"
  chmod 600 "$env_file"
  ok "Wrote $env_file (owner-readable only)."
}

# Docker refuses to start a container asking for more CPUs than the host has -
# a hard error, not a slow start - and on a small box the shipped memory
# ceilings add up to more RAM than exists. So a host that is smaller than the
# defaults were written for gets its own override file.
write_host_override() {
  local override="$INSTALL_DIR/compose.host.yml"
  local need_cpu=0 need_mem=0

  [ "$CPUS" -lt 4 ] && need_cpu=1
  [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 7800 ] && need_mem=1

  if [ "$need_cpu" = 0 ] && [ "$need_mem" = 0 ]; then
    rm -f "$override"
    return
  fi

  step "Sizing for this machine"
  local api_cpu browser_cpu
  api_cpu="$CPUS"; [ "$api_cpu" -gt 2 ] && api_cpu=2
  browser_cpu="$CPUS"; [ "$browser_cpu" -gt 4 ] && browser_cpu=4
  # Leave at least half a core: a browser pinned to every core starves the API
  # that has to answer while it mints.
  if [ "$CPUS" -le 2 ]; then api_cpu="1.5"; browser_cpu="1.5"; fi

  {
    printf '# Written by install/install.sh for this host: %s CPU, %s MiB RAM.\n' "$CPUS" "$MEM_MIB"
    printf '# docker/compose.yml is written for a 4 vCPU / 8 GiB machine. Its ceilings\n'
    printf '# are not reservations, but Docker refuses outright to start a container\n'
    printf '# that asks for more CPUs than exist, and on a small host the memory\n'
    printf '# ceilings add up to more than there is - so an unlucky mint takes the\n'
    printf '# OOM killer to PostgreSQL rather than to the process actually growing.\n'
    printf '#\n'
    printf '# Edit these freely. Delete the file to go back to the defaults.\n'
    printf 'services:\n'
    if [ "$need_mem" = 1 ]; then
      printf '  postgres:\n    mem_limit: %sm\n' "$(( MEM_MIB / 4 ))"
      printf '  redis:\n    mem_limit: %sm\n' "$(( MEM_MIB / 12 ))"
      printf '  api:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 9 ))" "$api_cpu"
      printf '  worker:\n    mem_limit: %sm\n' "$(( MEM_MIB / 9 ))"
      printf '  browser-rpc:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 3 ))" "$browser_cpu"
      printf '  downloader:\n    mem_limit: %sm\n    cpus: 1.0\n' "$(( MEM_MIB / 20 ))"
      # Flat rather than a share of RAM: it needs what the models and one
      # decoded file need (0.9 GiB measured on a 3-minute clip; an hour of
      # audio adds about 230 MB) whatever the host. No swap, as in compose,
      # because inference that pages is slower than inference that fails.
      printf '  transcriber:\n    mem_limit: 1536m\n    memswap_limit: 1536m\n'
    else
      printf '  api:\n    cpus: %s\n' "$api_cpu"
      printf '  worker:\n    cpus: %s\n' "$api_cpu"
      printf '  browser-rpc:\n    cpus: %s\n' "$browser_cpu"
      printf '  downloader:\n    cpus: 1.0\n'
    fi
  } >"$override"
  ok "Wrote compose.host.yml — ceilings scaled to $CPUS CPU and $MEM_MIB MiB."
}

# One entry point for every later command, because three arguments have to be
# right every time and forgetting one produces a confusing failure rather than
# an error: the project name, both compose files, and COMPOSE_ENV_FILES so that
# interpolation reads the .env in the repository root instead of docker/.env.
write_control_script() {
  local ctl="$INSTALL_DIR/dtkctl"
  local files="-f docker/compose.yml"
  [ -f "$INSTALL_DIR/compose.host.yml" ] && files="$files -f compose.host.yml"

  {
    printf '#!/usr/bin/env bash\n'
    printf '# The single entry point for this deployment. Written by install/install.sh.\n'
    printf '#\n'
    printf '#   ./dtkctl ps              what is running\n'
    printf '#   ./dtkctl logs -f api     follow the API log\n'
    printf '#   ./dtkctl restart api     restart one service\n'
    printf '#   ./dtkctl down            stop, keep the data\n'
    printf '#   ./dtkctl down -v         stop and delete the volumes. Irreversible.\n'
    printf '#   ./dtkctl upgrade         to the latest release: pull, migrate, restart\n'
    printf '#   ./dtkctl upgrade 5.1.6   to that release instead\n'
    printf '#\n'
    printf '# The three things it carries that are easy to forget: the project name, the\n'
    printf '# host override file, and COMPOSE_ENV_FILES - without which compose looks for\n'
    printf '# docker/.env and silently interpolates defaults.\n'
    printf '#\n'
    printf '# On published images the version is DTK_IMAGE_TAG in .env; upgrade writes it.\n'
    printf 'set -euo pipefail\n'
    # Single quotes on purpose: these expand when dtkctl runs, not now.
    # shellcheck disable=SC2016
    printf 'cd "$(dirname "$0")"\n'
    # Not a compose command, so it shadows nothing. Upgrading used to mean
    # editing DTK_IMAGE_TAG in .env by hand and then remembering the checkout,
    # the pull, the browser build and the migration, in that order. The
    # installer that came with the checkout knows them, and writes the tag.
    # shellcheck disable=SC2016
    printf 'if [ "${1:-}" = upgrade ]; then\n'
    # shellcheck disable=SC2016
    printf '  shift\n  DTK_PROJECT=%s DTK_INSTALL_DIR="$PWD" exec bash install/%s --upgrade "$@"\nfi\n' "$PROJECT" "$SCRIPT_NAME"
    printf 'export COMPOSE_ENV_FILES=.env\n'
    printf 'exec docker compose -p %s %s' "$PROJECT" "$files"
    [ "$WANT_BROWSER" = 1 ] && printf ' --profile browser'
    [ "$WANT_DOWNLOADER" = 1 ] && printf ' --profile downloader'
    [ "$WANT_TRANSCRIBER" = 1 ] && printf ' --profile transcriber'
    printf ' "$@"\n'
  } >"$ctl"
  chmod 755 "$ctl"
  ok "Wrote dtkctl — use it instead of raw docker compose."
}

# The first compose command of an install creates its networks, and Docker
# gives each one a subnet from a fixed set of pools. A host that has run a lot
# of stacks runs out - the networks of projects long gone keep theirs - and all
# compose says is one line about "address pools", which does not say what to do.
run_creating_networks() {
  local log status=0
  log="$(mktemp)" || die "Could not create a temporary file."
  TMP_FILES+=("$log")
  "$@" 2>&1 | tee "$log" || status=$?
  if [ "$status" != 0 ] && grep -qiE 'address pools have been fully subnetted|non-overlapping IPv4 address pool' "$log"; then
    warn "Docker has no free address range left for this install's networks."
    info "Every Docker network takes a subnet from a fixed set, and networks left behind"
    info "by stacks that are gone keep theirs. Remove the ones no container uses:"
    dim  "  docker network prune"
    info "If that frees nothing, give Docker more ranges with default-address-pools"
    info "in /etc/docker/daemon.json, restart Docker, and run this script again."
  fi
  return "$status"
}

bring_up() {
  step "Starting the stack"
  local ctl="$INSTALL_DIR/dtkctl"

  if [ "$USE_PUBLISHED" = 1 ]; then
    info "Pulling images. First time takes a few minutes."
    # Only the services that exist for this install: `pull downloader` on a
    # deployment that did not enable its profile is an error, not a no-op.
    local services=(api worker)
    [ "$WANT_DOWNLOADER" = 1 ] && services+=(downloader)
    # Deliberately not `a || b | tail || die`: a pipeline takes the exit status
    # of its LAST command, so `tail` succeeding would swallow a failed pull and
    # the install would carry on with no images.
    if ! "$ctl" pull "${services[@]}"; then
      die "Could not pull the images.
    From mainland China this is usually the registry, not the project - you
    probably need a mirror in /etc/docker/daemon.json. $DOCS_URL"
    fi
  fi

  if [ "$WANT_BROWSER" = 1 ]; then
    info "Building the browser image. This one is not published — expect several minutes."
    "$ctl" build browser-rpc || die "The browser image failed to build. Re-run without it, or see $DOCS_URL"
  fi

  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    if [ "$USE_PUBLISHED" = 1 ]; then
      info "Pulling the transcription image."
      "$ctl" pull transcriber || transcription_off "The transcription image could not be pulled.
    The release in use may predate it, or the registry did not answer."
    else
      info "Building the transcription image."
      "$ctl" build transcriber || transcription_off "The transcription image failed to build."
    fi
  fi

  info "Applying database migrations."
  run_creating_networks "$ctl" run --rm migrate || die "Migrations failed. Read the output above."

  info "Bringing services up."
  run_creating_networks "$ctl" up -d --wait || die "The stack did not come up healthy. Try: $ctl logs"
  ok "All services healthy."
}

show_result() {
  local ctl="$INSTALL_DIR/dtkctl" url=""
  step "Done"

  # The API prints a one-time setup URL on first boot. Pull it out of the log
  # rather than making somebody scroll for it.
  url="$("$ctl" logs api 2>/dev/null | grep -oE 'http://[^ ]+/setup\?token=[A-Za-z0-9_-]+' | tail -1 || true)"

  local shown_host="$BIND_HOST"
  [ "$shown_host" = "0.0.0.0" ] && shown_host="127.0.0.1"

  printf '\n'
  if [ -n "$url" ]; then
    # The API prints the address it binds to *inside* the container, which is
    # always 8000; the port somebody types is the published one. Rewrite the
    # authority and keep the token, rather than handing out a link to a port
    # nothing is listening on.
    url="${url#http://*/}"
    url="http://${shown_host}:${BIND_PORT}/${url}"
    printf '    %sOpen this to create the first administrator:%s\n' "$C_BOLD" "$C_RESET"
    printf '    %s%s%s\n' "$C_GREEN" "$url" "$C_RESET"
    dim "The link is good for 24 hours and stops working once the account exists."
  else
    printf '    %sConsole:%s http://%s:%s\n' "$C_BOLD" "$C_RESET" "$shown_host" "$BIND_PORT"
    dim "The one-time setup link is in the log: $ctl logs api"
  fi

  printf '\n'
  info "Directory   $INSTALL_DIR"
  info "Control     $ctl ps | logs -f api | restart api | down"
  info "Secrets     $INSTALL_DIR/.env  — back this up; it decrypts the database"
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    info "Models      $MODEL_PATH"
  fi
  info "Docs        $DOCS_URL"
  if [ "$WANT_BROWSER" = 0 ]; then
    printf '\n'
    dim "The browser container is off, so the identity pool will not fill itself."
    dim "Import your own cookies from the console: Identities → Import."
  fi
  if [ "$WANT_TRANSCRIBER" = 1 ] && [ "$WANT_BROWSER" = 1 ]; then
    printf '\n'
    dim "Transcription and the browser container share the CPU: a transcription"
    dim "that runs while an identity is being minted takes several times longer."
    dim "The first minutes, while the pool fills, are the busiest."
  fi
  if [ "$BIND_HOST" = "0.0.0.0" ]; then
    printf '\n'
    warn "This console is published on every interface without TLS."
    warn "Put a reverse proxy in front of it before it is reachable from anywhere else."
  fi
  printf '\n'
}

# ----------------------------------------------------------------- manage --
#
# Everything below runs against an install that already exists. One script for
# both jobs on purpose: somebody who deployed with it six months ago should not
# have to learn `docker compose` to change a password.
#
# Every action delegates to `dtkctl` or to the `dtk` CLI inside the api
# container. Nothing here reimplements what the project already does, so a menu
# entry cannot drift away from the command it stands for.

CTL=""   # path to the dtkctl of the install being managed

# Where an existing deployment lives, asked of Docker rather than guessed.
# Compose records the config file it was started from, and the install
# directory is that file's grandparent.
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

# What an existing install has turned on, read from its own dtkctl and .env.
# The menu used to take these from this run's environment, which knows nothing
# about that install: an upgrade pulled a downloader it never enabled, and
# would have skipped the transcriber it had.
load_install_state() {
  WANT_BROWSER=0
  WANT_DOWNLOADER=0
  WANT_TRANSCRIBER=0
  USE_PUBLISHED=0
  if grep -qF -- "--profile browser" "$CTL"; then WANT_BROWSER=1; fi
  if grep -qF -- "--profile downloader" "$CTL"; then WANT_DOWNLOADER=1; fi
  if grep -qF -- "--profile transcriber" "$CTL"; then WANT_TRANSCRIBER=1; fi
  if [ -n "$(env_value DTK_IMAGE)" ]; then USE_PUBLISHED=1; fi
  MODEL_PATH="$(env_value DTK_TRANSCRIBER_MODEL_PATH)"
  [ -n "$MODEL_PATH" ] || MODEL_PATH="${DTK_TRANSCRIBER_MODEL_PATH:-}"
}

# The version the running instance reports. The only one that counts: the
# checkout on disk can be ahead of the image that is actually serving.
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

# True when $1 is newer than $2. A pre-release suffix makes a version older
# than the same numbers without one, so 5.1.0.dev0 is behind 5.1.0 - the same
# rule web/src/lib/version.ts applies in the console.
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

# Read a password without echoing it, twice. The caller pipes it to a command
# on stdin; it is never an argument, because argv is readable by every process
# on the host.
read_password_twice() {
  local first second
  while true; do
    printf '    New password (at least 8 characters): ' >&2
    IFS= read -rs first <"$TTY_IN" || first=""
    printf '\n' >&2
    if [ "${#first}" -lt 8 ]; then
      warn "Too short."
      continue
    fi
    printf '    Again: ' >&2
    IFS= read -rs second <"$TTY_IN" || second=""
    printf '\n' >&2
    if [ "$first" != "$second" ]; then
      warn "They do not match."
      continue
    fi
    printf '%s' "$first"
    return 0
  done
}

# A destructive action asks for a word to be typed. A y/n in the same rhythm as
# five other y/n answers is not a decision.
confirm_word() {
  local word="$1" reply
  printf '    Type %s to confirm, anything else to cancel: ' "$word" >&2
  reply="$(read_line)"
  [ "$reply" = "$word" ]
}

pause_for_reader() {
  [ -n "$TTY_IN" ] || return 0
  printf '\n    Press enter to go back. ' >&2
  read_line >/dev/null
}

# --------------------------------------------------------------- actions --

show_status() {
  step "Status"
  local installed latest
  installed="$(running_version || true)"
  if [ -n "$installed" ]; then
    ok "Running version $installed"
  else
    warn "Nothing is running, or the API container is not answering."
  fi
  printf '\n'
  "$CTL" ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true

  printf '\n'
  info "Disk used by Docker:"
  docker system df 2>/dev/null | sed 's/^/      /'

  latest="$(latest_release)"
  if [ -n "$latest" ] && [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    printf '\n'
    warn "$latest is out; this instance is on $installed."
  elif [ -n "$latest" ]; then
    printf '\n'
    ok "Up to date with the latest release ($latest)."
  fi
}

# A GitHub release is tagged `v5.0.1`; the image it produces is `5.0.1`.
# docker/metadata-action's `{{version}}` strips the v, so pinning the release
# tag verbatim asks Docker Hub for something that was never published.
image_tag_for() {
  local target="$1"
  case "$target" in
    v[0-9]*) printf '%s' "${target#v}" ;;
    *) printf '%s' "$target" ;;
  esac
}

# Run with the version being moved to, so a pull or build in the middle of an
# upgrade asks for the new images while .env still names the old ones. A
# source install has no version to name: it keeps whatever tag .env gives it,
# and setting an empty one would build over the `dev` images instead.
with_target_tag() {
  if [ -n "$UPGRADE_TO" ]; then
    DTK_IMAGE_TAG="$UPGRADE_TO" "$@"
  else
    "$@"
  fi
}

# $1 is the published tag to move to, or empty on an install that builds from
# source, which follows the checkout instead.
do_upgrade() {
  UPGRADE_TO=""
  [ -z "${1:-}" ] || UPGRADE_TO="$(image_tag_for "$1")"
  step "Upgrading${UPGRADE_TO:+ to $UPGRADE_TO}"
  info "Your data is untouched: the named volumes survive this."
  printf '\n'

  info "Updating the checkout."
  git -C "$INSTALL_DIR" fetch --quiet origin main || warn "Could not fetch; using the checkout as it is."
  git -C "$INSTALL_DIR" merge --ff-only --quiet origin/main 2>/dev/null \
    || warn "The checkout has local changes; leaving it alone."

  # The rest is the new version's to do. This copy of the script is whatever
  # was on disk before the fetch, and it cannot know what the new compose file
  # needs - a service added since, a step that moved - so it hands over to the
  # one that came with the checkout. Once: the handover is marked.
  if [ -z "${DTK_UPGRADE_HANDOVER:-}" ] && [ -f "$INSTALL_DIR/install/$SCRIPT_NAME" ]; then
    info "Handing over to the installer that came with the new checkout."
    DTK_UPGRADE_HANDOVER=1 DTK_PROJECT="$PROJECT" DTK_INSTALL_DIR="$INSTALL_DIR" \
      exec bash "$INSTALL_DIR/install/$SCRIPT_NAME" --upgrade ${UPGRADE_TO:+"$UPGRADE_TO"}
  fi

  if [ "$USE_PUBLISHED" = 1 ]; then
    # Pull BEFORE pinning. Writing the tag first and then failing to fetch it
    # leaves the deployment pointing at an image that does not exist - the
    # stack keeps running on what is already up, but the next `up -d` cannot
    # start. The tag goes on the command for this pull only; .env is not
    # touched until the image is known to be there.
    info "Pulling."
    local services=(api worker)
    [ "$WANT_DOWNLOADER" = 1 ] && services+=(downloader)
    # Only a published transcriber is pulled. A local one is built by `up`,
    # because the tag it is asked for is new and so not on the machine yet.
    if [ "$WANT_TRANSCRIBER" = 1 ] && [ -n "$(env_value DTK_TRANSCRIBER_IMAGE)" ]; then
      services+=(transcriber)
    fi
    if ! with_target_tag "$CTL" pull "${services[@]}"; then
      die "Could not pull $UPGRADE_TO. Nothing was changed and the old version is still running.
    Check the tag exists: https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api/tags
    With transcription on, a release from before it has no transcriber image;
    turn transcription off from the menu first to go back that far."
    fi
  else
    # Nothing to pull: `pull` asks Docker Hub for dtk-app, which has never
    # existed, and every upgrade of a source install died right here. Its
    # images come from the checkout that was just updated, under the tag they
    # already have, so `up` has to be told about them by building.
    info "Building the images from the new checkout. Cached layers keep this short."
    local builds=(api)
    [ "$WANT_DOWNLOADER" = 1 ] && builds+=(downloader)
    [ "$WANT_TRANSCRIBER" = 1 ] && builds+=(transcriber)
    if ! "$CTL" build "${builds[@]}"; then
      die "The images failed to build. The old containers are still up; nothing was swapped."
    fi
  fi

  # The browser image is built here, never pulled, so an upgrade rebuilds it
  # against the new checkout - and an install from before the pin gets one
  # with a browser in it for the first time. Before the tag is pinned, for the
  # same reason as the pull.
  if [ "$WANT_BROWSER" = 1 ]; then
    ensure_browser_pin
    info "Building the browser image. Cached layers make this quick unless the pin changed."
    if ! with_target_tag "$CTL" build browser-rpc; then
      die "The browser image failed to build. Nothing was changed and the old version is still running."
    fi
  fi

  # Pin the tag rather than following `latest`: an operator should be able to
  # say which build is running, and to put it back.
  if [ -n "$UPGRADE_TO" ] && grep -q '^DTK_IMAGE_TAG=' "$INSTALL_DIR/.env"; then
    local tmp
    tmp="$(mktemp)" || die "Could not create a temporary file."
    TMP_FILES+=("$tmp")
    sed "s|^DTK_IMAGE_TAG=.*|DTK_IMAGE_TAG=$UPGRADE_TO|" "$INSTALL_DIR/.env" >"$tmp"
    cat "$tmp" >"$INSTALL_DIR/.env"
    rm -f "$tmp"
    ok "Pinned DTK_IMAGE_TAG=$UPGRADE_TO"
  fi
  # An install from before `./dtkctl upgrade` existed gets it now.
  write_control_script

  info "Migrating."
  "$CTL" run --rm migrate || die "Migrations failed. The old containers are still up; nothing was swapped."
  info "Restarting."
  "$CTL" up -d --wait || die "The new version did not come up healthy. Try: $CTL logs api"
  ok "Now on $(running_version || echo "${UPGRADE_TO:-the new checkout}")."
}

# The install an upgrade is for. Run from inside one - its own checkout's
# script, or from its directory - it is that one, whatever it is called: the
# project defaults to `dtk`, and looking that up from a deployment under
# another name found nothing, or someone else's.
install_to_upgrade() {
  local here
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    printf '%s' "$DTK_INSTALL_DIR"
    return 0
  fi
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")/.." 2>/dev/null && pwd || true)"
  if [ -n "$here" ] && [ -x "$here/dtkctl" ]; then
    printf '%s' "$here"
  elif [ -x "$PWD/dtkctl" ]; then
    printf '%s' "$PWD"
  else
    find_existing_install || true
  fi
}

# The project name a dtkctl was written with.
project_of() {
  sed -n 's/^exec docker compose -p \([A-Za-z0-9_-]*\) .*/\1/p' "$1" | head -n 1
}

# What `./dtkctl upgrade [VERSION]` runs. Nothing is asked: the version is the
# latest release unless one is named, the same default the menu offers.
upgrade_unattended() {
  if [ "$USE_PUBLISHED" = 0 ]; then
    if [ -n "$UPGRADE_TARGET" ]; then
      die "This install builds its images from source, so it runs what the checkout holds
    and there is no published version to choose. Run ./dtkctl upgrade on its own."
    fi
    do_upgrade ""
    return 0
  fi

  # Handed over from an upgrade already under way: what to move to was
  # decided before the checkout changed, including "the same one again".
  if [ -n "${DTK_UPGRADE_HANDOVER:-}" ]; then
    do_upgrade "$UPGRADE_TARGET"
    return 0
  fi

  local target="$UPGRADE_TARGET" installed
  if [ -z "$target" ]; then
    target="$(latest_release)"
    if [ -z "$target" ]; then
      die "Could not ask GitHub which release is the latest. Name it instead:
      ./dtkctl upgrade 5.1.6"
    fi
  fi
  target="$(image_tag_for "$target")"
  installed="$(running_version || true)"
  if [ -n "$installed" ] && [ "$target" = "$installed" ]; then
    ok "Already on $target. Nothing to do."
    return 0
  fi
  # Only between two version numbers: `latest` or a sha- build has no order.
  case "$target" in
    [0-9]*)
      if [ -n "$installed" ] && version_is_newer "$installed" "$target"; then
        warn "$target is older than the $installed that is running."
        info "Migrations do not run backwards. If the old version will not start on the"
        info "newer schema, restore a backup taken while it was running."
      fi ;;
  esac
  do_upgrade "$target"
}

act_change_password() {
  step "Change a password"
  "$CTL" exec -T api dtk user list 2>/dev/null | sed 's/^/    /' || warn "Could not list accounts."
  printf '\n'
  local who password
  who="$(ask_value "Which account?" "admin")"
  [ -n "$who" ] || return 0
  password="$(read_password_twice)"
  if printf '%s' "$password" | "$CTL" exec -T api dtk user passwd "$who" --stdin; then
    ok "Password changed for $who."
  else
    warn "That did not work. Check the account name above."
  fi
  unset password
}

act_add_admin() {
  step "Add an administrator"
  dim "There is no rename: accounts are created and their passwords reset, not"
  dim "renamed. To retire a name, make the new account and stop using the old."
  printf '\n'
  local who password
  who="$(ask_value "New account name?" "")"
  [ -n "$who" ] || { warn "No name given."; return 0; }
  password="$(read_password_twice)"
  if printf '%s' "$password" | "$CTL" exec -T api dtk user create "$who" --role admin --stdin; then
    ok "Created $who as an administrator."
  else
    warn "That did not work. The name may already be taken."
  fi
  unset password
}

act_list_users() {
  step "Accounts"
  "$CTL" exec -T api dtk user list 2>/dev/null | sed 's/^/    /' || warn "Could not list accounts."
}

act_backup() {
  step "Back up"
  dim "The archive lands in the backup volume, which survives a container rebuild."
  # -o is not optional here. Without it the CLI writes beside the working
  # directory, and the container's root filesystem is read-only by design, so
  # the backup fails with "Read-only file system" and nothing says why.
  # DTK_BACKUP_DIR is resolved inside the container so it follows compose.
  # Single quotes: DTK_BACKUP_DIR expands inside the container, not here.
  # shellcheck disable=SC2016
  if "$CTL" exec -T api sh -c 'dtk backup create -o "$DTK_BACKUP_DIR"'; then
    printf '\n'
    # Single quotes: DTK_BACKUP_DIR expands inside the container, not here.
    # shellcheck disable=SC2016
    "$CTL" exec -T api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' || true
  else
    warn "The backup failed. Read the output above."
  fi
}

act_restore() {
  step "Restore"
  # Single quotes: DTK_BACKUP_DIR expands inside the container, not here.
  # shellcheck disable=SC2016
  "$CTL" exec -T api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' \
    || { warn "No archives found."; return 0; }
  printf '\n'
  warn "Restoring replaces the current database. What is in it now is gone."
  local archive
  archive="$(ask_value "Archive path (as listed above)?" "")"
  [ -n "$archive" ] || return 0
  confirm_word "restore" || { info "Cancelled."; return 0; }
  "$CTL" exec -T api dtk backup restore "$archive" --yes || warn "The restore failed. Read the output above."
}

act_diagnose() {
  step "Self check"
  "$CTL" exec -T api dtk diagnose 2>&1 | sed 's/^/    /' || warn "The self check could not run."
}

act_logs() {
  step "Logs"
  dim "Last 60 lines of each service. Follow them live with: $CTL logs -f api"
  printf '\n'
  "$CTL" logs --tail 60 2>&1 | tail -80
}

act_restart() {
  step "Restart"
  "$CTL" restart || warn "Restart failed."
  "$CTL" ps --format "table {{.Service}}\t{{.Status}}" 2>/dev/null || true
}

act_setting() {
  step "Settings"
  dim "Runtime settings live in the database and take effect without a restart."
  dim "Retention, for example: retention.request_log_days, retention.task_days."
  printf '\n'
  local key value
  key="$(ask_value "Setting name (empty to list them all)?" "")"
  if [ -z "$key" ]; then
    "$CTL" exec -T api dtk config list 2>&1 | sed 's/^/    /' | head -80
    return 0
  fi
  "$CTL" exec -T api dtk config get "$key" 2>&1 | sed 's/^/    /' || { warn "No such setting."; return 0; }
  printf '\n'
  value="$(ask_value "New value (empty to leave it)?" "")"
  [ -n "$value" ] || return 0
  "$CTL" exec -T api dtk config set "$key" "$value" 2>&1 | sed 's/^/    /' || warn "Rejected. The validator says why above."
}

# Disk. The thing that actually grows here is images and build cache, not logs:
# every service pins json-file to 10m x 3, so container logs cannot pass about
# 180 MB for the whole stack. Old image tags and buildx cache have no such
# ceiling.
#
# Scoped to this project on purpose. A plain `docker image prune -a` would take
# unrelated images off a host that runs other stacks, which is not this
# script's to decide.
act_free_disk() {
  step "Free disk space"
  info "Before:"
  docker system df 2>/dev/null | sed 's/^/      /'

  printf '\n'
  dim "Container logs are already capped at 10 MB x 3 per service by the compose"
  dim "file. What grows without a ceiling is old image tags and build cache."
  printf '\n'

  # In use means referenced by a container that exists, running or stopped -
  # not "matches the tag in .env". The .env of a build-from-source install has
  # no tag at all, and with that as the filter this offered to delete the image
  # the stack was serving from. Docker would have refused while it was up, and
  # obliged the moment it was down.
  local in_use images=""
  in_use="$(docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)"
  images="$(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null \
    | grep -E '^(evil0ctal/douyin_tiktok_download_api|dtk-browser-rpc|dtk-app|dtk-downloader|dtk-transcriber)' \
    | while read -r ref id; do
        printf '%s\n' "$in_use" | grep -qxF "$ref" || printf '%s %s\n' "$ref" "$id"
      done || true)"

  if [ -n "$images" ]; then
    info "Images from this project that are not the version in use:"
    printf '%s\n' "$images" | sed 's/^/      /'
    printf '\n'
    if ask_yes_no "Remove them?" "y"; then
      printf '%s\n' "$images" | awk '{print $2}' | sort -u | while read -r id; do
        docker rmi "$id" >/dev/null 2>&1 && info "removed $id" || true
      done
    fi
  else
    ok "No stale images from this project."
  fi

  printf '\n'
  dim "Build cache speeds up rebuilding the browser image enormously - it is why"
  dim "a rebuild after a small change takes seconds. Clearing it costs that."
  if ask_yes_no "Clear the build cache too?" "n"; then
    docker builder prune -af 2>&1 | tail -2 | sed 's/^/    /'
  fi

  printf '\n'
  info "After:"
  docker system df 2>/dev/null | sed 's/^/      /'
}

# Transcription is the optional part most installs will want to add later, so
# it is the one the menu can switch. Both directions touch only the lines they
# own in .env and dtkctl, then recreate the stack: api and worker read the
# transcriber's address once, at start.
act_transcription() {
  step "Transcription"
  if [ "$WANT_TRANSCRIBER" = 1 ]; then
    ok "On. Models: ${MODEL_PATH:-not recorded in .env}"
    "$CTL" ps transcriber --format "table {{.Service}}\t{{.Status}}" 2>/dev/null | sed 's/^/    /' || true
    printf '\n'
    dim "Turning it off keeps every transcript already made, and the models on disk."
    if ! ask_yes_no "Turn it off?" "n"; then
      return 0
    fi
    "$CTL" rm -sf transcriber >/dev/null 2>&1 || true
    set_env_value DTK_TRANSCRIBER_URL ""
    set_ctl_profile transcriber off
    WANT_TRANSCRIBER=0
    "$CTL" up -d --wait || { warn "The stack did not come back healthy. Try: $CTL logs"; return 0; }
    ok "Transcription is off. Delete ${MODEL_PATH:-the models} yourself if you want the space."
    return 0
  fi

  if [ "$WANT_DOWNLOADER" = 0 ]; then
    warn "Transcription works on stored videos, and this install has media downloads off."
    return 0
  fi
  if [ "$CPUS" -lt 2 ]; then
    warn "Transcription needs two cores; this machine has one."
    return 0
  fi
  dim "Offline transcription turns stored videos into text on this machine, with"
  dim "FunASR models trained for Mandarin. About 1 GiB of RAM and two cores while"
  dim "it works, and 500 MB of models that this can download and check for you."
  if ! ask_yes_no "Turn it on?" "y"; then
    return 0
  fi
  WANT_TRANSCRIBER=1
  choose_model_path
  fetch_models
  if [ "$WANT_TRANSCRIBER" = 0 ]; then
    return 0
  fi

  # The image before any file changes: a release from before transcription
  # has no transcriber tag, and finding that out after .env says "on" would
  # leave an install that cannot start.
  local image
  image="$(env_value DTK_TRANSCRIBER_IMAGE)"
  if [ -z "$image" ] && [ "$USE_PUBLISHED" = 1 ]; then image="$TRANSCRIBER_IMAGE"; fi
  if [ -n "$image" ]; then
    info "Pulling the transcription image."
    if ! DTK_TRANSCRIBER_IMAGE="$image" "$CTL" --profile transcriber pull transcriber; then
      warn "Could not pull it. The version this install runs may predate transcription"
      warn "(upgrade first, 2 in the main menu), or the registry did not answer."
      WANT_TRANSCRIBER=0
      return 0
    fi
  else
    info "Building the transcription image."
    if ! "$CTL" --profile transcriber build transcriber; then
      warn "The transcription image failed to build. Nothing was changed."
      WANT_TRANSCRIBER=0
      return 0
    fi
  fi

  write_transcriber_env
  set_ctl_profile transcriber on
  info "Starting it, and recreating api and worker so they know where it is."
  if ! "$CTL" up -d --wait; then
    warn "The stack did not come up healthy. The reason is usually in: $CTL logs transcriber"
    return 0
  fi
  ok "Transcription is on. Open a stored video in the console and choose Transcribe."
}

# ----------------------------------------------------------------- menus --

menu_uninstall() {
  while true; do
    step "Stop or remove"
    info "  1  Stop the stack, keep every byte of data"
    info "  2  Stop and delete the data volumes - database, media, backups"
    info "  3  As 2, and remove $INSTALL_DIR as well"
    info "  b  Back"
    case "$(ask_choice "Which?")" in
      1) "$CTL" down --remove-orphans && ok "Stopped. Start it again with: $CTL up -d" ; return 0 ;;
      2)
        warn "This deletes the database, the media volume and every backup in it."
        confirm_word "delete" || { info "Cancelled."; continue; }
        "$CTL" down -v --remove-orphans && ok "Stopped and the volumes are gone."
        return 0 ;;
      3)
        warn "This deletes the volumes AND $INSTALL_DIR, including your .env."
        warn "Without that .env the data could not be decrypted anyway."
        confirm_word "delete" || { info "Cancelled."; continue; }
        "$CTL" down -v --remove-orphans || true
        # Guarded rather than trusting the variable: this is the one place the
        # script removes a tree, and an empty or silly value must not reach rm.
        case "$INSTALL_DIR" in
          ""|"/"|"/usr"|"/etc"|"/var"|"/home"|"/root"|"/opt")
            die "Refusing to remove $INSTALL_DIR." ;;
        esac
        [ -f "$INSTALL_DIR/docker/compose.yml" ] || die "$INSTALL_DIR does not look like an install; not removing it."
        rm -rf -- "$INSTALL_DIR" && ok "Removed $INSTALL_DIR."
        return 0 ;;
      b|"") return 0 ;;
      *) warn "Pick 1, 2, 3 or b." ;;
    esac
  done
}

menu_manage() {
  while true; do
    step "Manage"
    info "  1  Change a password          6  Self check"
    info "  2  Add an administrator       7  Logs"
    info "  3  List accounts              8  Restart services"
    info "  4  Back up now                9  Settings"
    info "  5  Restore a backup          10  Free disk space"
    info "                               11  Transcription on or off"
    info "  b  Back"
    case "$(ask_choice "Which?")" in
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
      *) warn "Pick a number from the list, or b." ;;
    esac
  done
}

menu_existing() {
  local installed latest upgrade_line=""
  installed="$(running_version || true)"
  latest="$(latest_release)"

  step "An install is already here"
  ok "$INSTALL_DIR"
  if [ -n "$installed" ]; then
    ok "Running $installed"
  else
    warn "Not running, or the API is not answering yet."
  fi
  if [ -n "$latest" ] && [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    upgrade_line="$latest"
    warn "$latest is available."
  fi

  while true; do
    printf '\n'
    info "  1  Status - versions, containers, disk"
    if [ "$USE_PUBLISHED" = 0 ]; then
      info "  2  Update the checkout and rebuild"
    elif [ -n "$upgrade_line" ]; then
      info "  2  Upgrade to $upgrade_line"
    else
      info "  2  Reinstall the current version (pull, migrate, restart)"
    fi
    info "  3  Manage - passwords, backups, settings, disk"
    info "  4  Stop or remove this install"
    info "  q  Quit"
    case "$(ask_choice "Which?")" in
      1) show_status; pause_for_reader ;;
      2)
        local target current
        current="$(grep '^DTK_IMAGE_TAG=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        if [ "$USE_PUBLISHED" = 0 ]; then
          # Built from source, so it runs what the checkout holds. A tag typed
          # here used to be pulled from Docker Hub as dtk-app, which fails.
          do_upgrade ""
          pause_for_reader
          continue
        fi
        if [ -n "$upgrade_line" ]; then
          target="$(ask_value "Which tag?" "$upgrade_line")"
        else
          # No newer release does not mean nowhere to go. An instance pinned to
          # a sha- build - which is what the README tells you to do in
          # production - sits here permanently otherwise: `latest` follows
          # releases, and a release only appears when somebody cuts one. The
          # same field takes an older tag, which is how you roll back.
          dim "No newer release. You can still move to another published tag:"
          dim "a sha-… build, or latest. Docker Hub lists what exists."
          target="$(ask_value "Which tag?" "${current:-latest}")"
        fi
        if [ -n "$target" ] && [ "$target" != "$current" ]; then
          do_upgrade "$target"
        elif [ -n "$target" ]; then
          info "Already on $target. Re-pulling and restarting anyway."
          do_upgrade "$target"
        fi
        pause_for_reader ;;
      3) menu_manage ;;
      4) menu_uninstall; return 0 ;;
      q|"") return 0 ;;
      *) warn "Pick 1, 2, 3, 4 or q." ;;
    esac
  done
}

# -------------------------------------------------------------------- main --

usage() {
  cat <<EOF
Usage: bash install.sh [options]

  --yes, -y      Take the default answer to every question. Publishes on
                 127.0.0.1:8000, downloader on, browser container and
                 transcription off.
  --check        Detect the system and print what would happen, then stop.
                 Changes nothing.
  --manage       Go straight to the menu for an install that already exists.
  --upgrade [VERSION]
                 Upgrade the install that already exists, asking nothing: to
                 the latest release, or to VERSION. ./dtkctl upgrade runs this.
  --help, -h     This text.

Run it with no options and it works out which job it is doing: it installs when
there is nothing here, and opens a menu when there is - status, upgrade,
passwords, backups, settings, disk.

Environment (each one presets an answer, so --yes becomes unattended):
  DTK_PROJECT            Compose project name. Default: dtk.
  DTK_INSTALL_DIR        Where to install. Default: /opt/dtk as root, ~/dtk otherwise.
  DTK_BIND_HOST          127.0.0.1 (default) or 0.0.0.0.
  DTK_BIND_PORT          Default: 8000.
  DTK_ENABLE_BROWSER     1 to build the browser container, 0 (default) not to.
  DTK_ENABLE_DOWNLOADER  1 (default) for media downloads, 0 without.
  DTK_ENABLE_TRANSCRIBER 1 for offline transcription, 0 (default) without.
                         Needs media downloads and two cores.
  DTK_TRANSCRIBER_MODEL_PATH
                         Where its models are, or are to be downloaded to.
                         Default: <install directory>/models/funasr-onnx.
  CLOAKBROWSER_COMMIT    The CloakBrowser commit the browser image installs.
                         Default: $CLOAKBROWSER_PIN.
  DTK_USE_PUBLISHED      1 (default) to pull images, 0 to build from source.
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)   ASSUME_YES=1 ;;
      --check)    CHECK_ONLY=1 ;;
      --manage)   MANAGE_ONLY=1 ;;
      --upgrade)
        UPGRADE_ONLY=1
        # The version is optional, so only a next word that is not an option.
        if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then
          UPGRADE_TARGET="$2"
          shift
        fi ;;
      -h|--help)  usage; exit 0 ;;
      *)          die "Unknown option: $1  (try --help)" ;;
    esac
    shift
  done

  # `./dtkctl upgrade` is a command someone types into a running install,
  # not an install: the banner and the host report are noise there.
  if [ "$UPGRADE_ONLY" = 0 ]; then
    banner
  fi
  detect_os
  detect_resources
  if [ "$UPGRADE_ONLY" = 0 ]; then
    report_host
  fi

  step "Checking Docker"
  if ! check_docker_present; then
    if [ "$CHECK_ONLY" = 1 ]; then
      warn "Docker is not installed. The script would offer to install it."
      info "Package manager command for this system:"
      dim  "  $(docker_install_hint)"
      exit 0
    fi
    offer_docker_install
  fi
  ok "Docker $(docker --version 2>/dev/null | sed 's/Docker version //; s/,.*//')"

  if [ "$CHECK_ONLY" = 1 ]; then
    check_compose || true
    if check_docker_running; then ok "The Docker daemon is answering."; else warn "The Docker daemon is not answering."; fi
    step "Check only"
    info "Nothing was changed. Run without --check to install."
    exit 0
  fi

  ensure_docker_running
  check_compose || die "Compose v2 $COMPOSE_MIN or newer is required."

  if [ "$UPGRADE_ONLY" = 1 ]; then
    INSTALL_DIR="$(install_to_upgrade)"
    if [ -z "$INSTALL_DIR" ] || [ ! -x "$INSTALL_DIR/dtkctl" ]; then
      die "No install found for project '$PROJECT'.
    Set DTK_INSTALL_DIR to where it lives, or run ./dtkctl upgrade from there."
    fi
    CTL="$INSTALL_DIR/dtkctl"
    PROJECT="$(project_of "$CTL")"
    [ -n "$PROJECT" ] || die "Could not read the project name from $CTL."
    load_install_state
    upgrade_unattended
    exit 0
  fi

  # An install that is already here turns this into a management tool rather
  # than an installer. Asked of Docker, so it is found wherever it was put -
  # not only in the two directories this script would have suggested.
  local found=""
  if [ -z "${DTK_INSTALL_DIR:-}" ] && [ "$ASSUME_YES" = 0 ]; then
    found="$(find_existing_install || true)"
  elif [ "$MANAGE_ONLY" = 1 ]; then
    found="${DTK_INSTALL_DIR:-$(find_existing_install || true)}"
  fi
  if [ "$MANAGE_ONLY" = 1 ] && { [ -z "$found" ] || [ ! -x "$found/dtkctl" ]; }; then
    die "No install found for project '$PROJECT'.
    Run this script without --manage to create one, or set DTK_INSTALL_DIR
    to where it lives."
  fi
  if [ -n "$found" ] && [ -x "$found/dtkctl" ]; then
    INSTALL_DIR="$found"
    CTL="$found/dtkctl"
    load_install_state
    if [ -z "$TTY_IN" ]; then
      warn "An install is already at $INSTALL_DIR."
      info "Run this from a terminal to manage it, or use $CTL directly."
      exit 0
    fi
    menu_existing
    exit 0
  fi

  if [ -z "$TTY_IN" ] && [ "$ASSUME_YES" = 0 ]; then
    die "No terminal to ask questions on.
    Either download and run the script:
      curl -fsSL $REPO_RAW/install/install.sh -o install.sh && bash install.sh
    or accept every default:
      curl -fsSL $REPO_RAW/install/install.sh | bash -s -- --yes"
  fi

  choose_directory
  prepare_directory
  ask_questions
  fetch_models
  write_env
  write_host_override
  write_control_script
  CTL="$INSTALL_DIR/dtkctl"
  bring_up
  show_result
}

main "$@"
