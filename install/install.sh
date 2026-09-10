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

# Anything we create outside the install directory is registered here, so an
# interrupt does not leave it behind.
TMP_FILES=()
cleanup() {
  local f
  for f in "${TMP_FILES[@]:-}"; do [ -n "$f" ] && rm -f "$f"; done
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

  printf '\n'
  dim "The downloader saves media to a Docker volume on this host. Small and"
  dim "cheap; without it, download requests answer 501 and say why."
  local dl_default="y"; [ "$WANT_DOWNLOADER" = 0 ] && dl_default="n"
  if ask_yes_no "Enable media downloads?" "$dl_default"; then WANT_DOWNLOADER=1; else WANT_DOWNLOADER=0; fi

  printf '\n'
  dim "Published images are pulled and start in about a minute. Building from"
  dim "source takes several minutes and needs npm and uv to reach the network."
  local pub_default="y"; [ "$USE_PUBLISHED" = 0 ] && pub_default="n"
  if ask_yes_no "Use the published images (recommended)?" "$pub_default"; then USE_PUBLISHED=1; else USE_PUBLISHED=0; fi

}

write_env() {
  step "Writing .env"
  local env_file="$INSTALL_DIR/.env"

  if [ -f "$env_file" ]; then
    ok "Keeping the existing .env — its secrets are what the database was built with."
    info "Delete it yourself if you want a fresh one, but the old volumes will not open."
    return
  fi

  local pg_pass redis_pass secret dl_token
  pg_pass="$(random_hex 24)"
  redis_pass="$(random_hex 24)"
  secret="$(random_b64 48)"
  dl_token="$(random_hex 24)"

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
    printf '#\n'
    printf '# The three things it carries that are easy to forget: the project name, the\n'
    printf '# host override file, and COMPOSE_ENV_FILES - without which compose looks for\n'
    printf '# docker/.env and silently interpolates defaults.\n'
    printf 'set -euo pipefail\n'
    # Single quotes on purpose: these expand when dtkctl runs, not now.
    # shellcheck disable=SC2016
    printf 'cd "$(dirname "$0")"\n'
    printf 'export COMPOSE_ENV_FILES=.env\n'
    printf 'exec docker compose -p %s %s' "$PROJECT" "$files"
    [ "$WANT_BROWSER" = 1 ] && printf ' --profile browser'
    [ "$WANT_DOWNLOADER" = 1 ] && printf ' --profile downloader'
    printf ' "$@"\n'
  } >"$ctl"
  chmod 755 "$ctl"
  ok "Wrote dtkctl — use it instead of raw docker compose."
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

  info "Applying database migrations."
  "$ctl" run --rm migrate || die "Migrations failed. Read the output above."

  info "Bringing services up."
  "$ctl" up -d --wait || die "The stack did not come up healthy. Try: $ctl logs"
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
  info "Docs        $DOCS_URL"
  if [ "$WANT_BROWSER" = 0 ]; then
    printf '\n'
    dim "The browser container is off, so the identity pool will not fill itself."
    dim "Import your own cookies from the console: Identities → Import."
  fi
  if [ "$BIND_HOST" = "0.0.0.0" ]; then
    printf '\n'
    warn "This console is published on every interface without TLS."
    warn "Put a reverse proxy in front of it before it is reachable from anywhere else."
  fi
  printf '\n'
}

# -------------------------------------------------------------------- main --

usage() {
  cat <<EOF
Usage: bash install.sh [options]

  --yes, -y      Take the default answer to every question. Publishes on
                 127.0.0.1:8000, downloader on, browser container off.
  --check        Detect the system and print what would happen, then stop.
                 Changes nothing.
  --help, -h     This text.

Environment (each one presets an answer, so --yes becomes unattended):
  DTK_PROJECT            Compose project name. Default: dtk.
  DTK_INSTALL_DIR        Where to install. Default: /opt/dtk as root, ~/dtk otherwise.
  DTK_BIND_HOST          127.0.0.1 (default) or 0.0.0.0.
  DTK_BIND_PORT          Default: 8000.
  DTK_ENABLE_BROWSER     1 to build the browser container, 0 (default) not to.
  DTK_ENABLE_DOWNLOADER  1 (default) for media downloads, 0 without.
  DTK_USE_PUBLISHED      1 (default) to pull images, 0 to build from source.
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)   ASSUME_YES=1 ;;
      --check)    CHECK_ONLY=1 ;;
      -h|--help)  usage; exit 0 ;;
      *)          die "Unknown option: $1  (try --help)" ;;
    esac
    shift
  done

  banner
  detect_os
  detect_resources
  report_host

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
  write_env
  write_host_override
  write_control_script
  bring_up
  show_result
}

main "$@"
