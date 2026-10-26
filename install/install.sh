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

# DTK_REPO_URL points the clone at a mirror. Releases are found on the
# checkout's own origin once there is one, so it stays the mirror after.
# DTK_GH_PROXY prefixes both GitHub addresses below with one mirror, none by
# default. REPO_RAW is never fetched here, only printed in the download
# commands a message suggests, so what the prefix moves is the clone - and
# with DTK_REPO_URL set, the clone stays with that. Whatever relays the clone
# hands over the whole install, so run one you trust: a self-hosted relay that
# allows only this repository (a Cloudflare Worker, say) is the clean form.
# A trailing slash is fine.
gh_proxy="${DTK_GH_PROXY:-}"
while [ "$gh_proxy" != "${gh_proxy%/}" ]; do gh_proxy="${gh_proxy%/}"; done
# https, and nothing that is shell syntax: REPO_RAW is written into a
# double-quoted string in the generated dtkctl, as DTK_PROJECT is below. A
# prefix without a scheme used to get as far as git ("protocol
# 'gh-proxy.com/https' is not supported"), and an http:// one cloned the
# install in plain text, for anyone on the path to rewrite.
if [ -n "$gh_proxy" ]; then
  case "${gh_proxy#https://}" in
    "$gh_proxy"|""|[![:alnum:]]*|*[![:alnum:]._~:/?=%+@-]*)
      printf 'error: DTK_GH_PROXY must be an https:// address, like https://mirror.example/, of letters, digits and . _ ~ : / ? = %% + @ - only.\n' >&2
      exit 1 ;;
  esac
fi
readonly GH_PROXY="${gh_proxy:+$gh_proxy/}"
readonly GITHUB_REPO_URL="https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git"
readonly REPO_URL="${DTK_REPO_URL:-${GH_PROXY}${GITHUB_REPO_URL}}"
readonly REPO_RAW="${GH_PROXY}https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main"
readonly DOCKER_INSTALL_URL="https://get.docker.com"
readonly COMPOSE_INSTALL_URL="https://docs.docker.com/compose/install/linux/"
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

# Every CloakBrowser commit this project ever wrote into an .env: 5.1.4 to
# 5.1.6 wrote 0.6.3's at install, and .env.example - which manual installs
# copy - carried 0.5.10's before that. A frozen copy kept an install on 0.5.10
# after the checkout's requirements had moved to Playwright 1.64, so an upgrade
# takes these out (settle_browser_pin). The list is closed: nothing writes the
# default into .env any more, so a later pin never needs adding here.
readonly CLOAKBROWSER_WRITTEN_PINS="f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce fbf005bc199920b592df17ac4d489324e29d56d8"

# Overridable for testing, so a trial run cannot touch a real `dtk` stack.
# Validated because it is written into the generated dtkctl: anything that is
# not a plain name would end up as shell syntax there. Compose's own rule, not
# a looser one: `MyDtk` passed here and failed at the first pull, blamed on the
# registry.
PROJECT="${DTK_PROJECT:-dtk}"
case "$PROJECT" in
  ''|[!a-z0-9]*|*[!a-z0-9_-]*)
    printf 'error: DTK_PROJECT must be lowercase letters, digits, dash or underscore, starting with a letter or digit.\n' >&2
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
NO_BACKUP=0

# Anything we create outside the install directory is registered here, so an
# interrupt does not leave it behind.
TMP_FILES=()
# Set while an upgrade has moved the checkout but not yet swapped the stack:
# leaving the script then - an error, Ctrl-C - puts the checkout and .env back
# (undo_upgrade), so "nothing was changed" is true when it is said.
UPGRADE_UNDO=""
# Set once a password has been read with echo off. A Ctrl-C inside that read
# left the terminal not echoing anything typed afterwards.
SECRET_PROMPTED=0
# It has to end in success. A script that runs off its end exits with the
# status of the last command it ran, which is this trap; with nothing
# registered, `[ -n "" ]` was that command, and every install that needed no
# temporary file reported failure after printing "Done".
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
# INT and TERM end the script. They used to run cleanup and nothing else, and a
# trap that returns lets the script carry on: Ctrl-C at "Enable the browser
# container? [Y/n]" took the default and went on to a 6 GB build.
trap 'exit 130' INT
trap 'exit 143' TERM

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
    # A preset is held to the same rule as a typed answer. Under --yes nobody
    # is there to retype it, and a bad port or path used to surface only at
    # the first `docker compose pull`, as a message about the registry.
    if [ -n "$validator" ] && ! "$validator" "$default"; then
      die "'$default' does not work for \"$prompt\", and there is nobody to ask. Change the preset and run this again."
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

# One line as typed, with only the ends trimmed. ask_value takes every space
# out, which suits a port or a path and mangled a setting: `GET /api/v1/x` was
# stored as `GET/api/v1/x`, and the validator accepted it.
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

# A leading ~ expanded. It arrives literal from `read`, so this is a pattern
# match on a character, not an attempt at expansion.
# shellcheck disable=SC2088
expand_tilde() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s' "$HOME/${1#\~/}" ;;
    *) printf '%s' "$1" ;;
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
    have sudo || die "This needs root, and sudo is not installed: $(printf '%q ' "$@")"
    # %q, not "$*": with IFS set to newline and tab, "$*" printed each word of
    # the command on a line of its own.
    dim "sudo $(printf '%q ' "$@")"
    sudo "$@"
  fi
}

# --------------------------------------------------------------- detection --

OS=""          # linux | macos | wsl | unsupported
DISTRO=""      # debian, ubuntu, fedora, rhel, arch, alpine, opensuse …
DISTRO_LIKE="" # ID_LIKE from os-release
DISTRO_VERSION="" # VERSION_ID from os-release
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

# What the Docker daemon can hand out, which is what the ceilings have to fit.
# On Docker Desktop, OrbStack or a remote DOCKER_HOST that is a VM, not this
# host: a Mac with 16 cores in front of a 2-CPU VM wrote no override, and
# browser-rpc asked for 4.0 CPUs, which Docker refuses outright.
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
    ok "Docker can use $cpus CPU and $(( mib / 1024 )).$(( (mib % 1024) * 10 / 1024 )) GiB here, so that is what the stack is sized for."
  fi
  CPUS="$cpus"
  MEM_MIB="$mib"
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
      info "Install a newer Compose plugin, then run this again: $COMPOSE_INSTALL_URL"
      return 1
    fi
    ok "Docker Compose $raw"
    return 0
  fi
  if have docker-compose; then
    warn "Found the old standalone docker-compose (v1)."
    info "This stack uses Compose v2 features. Install the Compose plugin:"
    info "  $COMPOSE_INSTALL_URL"
    return 1
  fi
  warn "Docker Compose v2 is not installed."
  info "Install the Compose plugin: $COMPOSE_INSTALL_URL"
  return 1
}

# Docker and Compose $COMPOSE_MIN or newer from the distribution's own
# packages where those are new enough, else where Docker documents its own
# repository. Checked against each archive in October 2026: Debian 12 has only
# Compose v1, openSUSE Leap 15.6 has Compose 2.22, and on the RHEL family
# `docker` is podman's shim. This used to say `dnf install docker docker-compose-plugin` for all
# of RHEL, and that plugin package exists only in Docker's repository, so dnf
# refused the whole line even on Fedora, which packages both under other names.
docker_install_hint() {
  local docs="https://docs.docker.com/engine/install"
  case "$DISTRO" in
    debian)
      case "$DISTRO_VERSION" in
        1[3-9]|[2-9][0-9]) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose docker-buildx" ;;
        *) printf '%s' "see $docs/debian/" ;;
      esac
      return ;;
    raspbian) printf '%s' "see $docs/raspberry-pi-os/"; return ;;
    fedora)   printf '%s' "sudo dnf install -y moby-engine docker-compose docker-buildx"; return ;;
    rhel)     printf '%s' "see $docs/rhel/"; return ;;
    amzn)     printf '%s' "sudo dnf install -y docker, then the Compose plugin: $COMPOSE_INSTALL_URL"; return ;;
  esac
  case "$(distro_family)" in
    debian)
      case " $DISTRO $DISTRO_LIKE " in
        *" ubuntu "*) printf '%s' "sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 docker-buildx" ;;
        *) printf '%s' "see $docs/debian/" ;;
      esac ;;
    rhel) printf '%s' "see $docs/centos/" ;;
    suse)
      case "$DISTRO_VERSION" in
        1[0-5]|1[0-5].*) printf '%s' "sudo zypper install -y docker, then the Compose plugin: $COMPOSE_INSTALL_URL" ;;
        *) printf '%s' "sudo zypper install -y docker docker-compose docker-buildx" ;;
      esac ;;
    arch)   printf '%s' "sudo pacman -S --needed docker docker-compose docker-buildx" ;;
    alpine) printf '%s' "sudo apk add docker docker-cli-compose docker-cli-buildx && sudo rc-update add docker default" ;;
    nixos)  printf '%s' "add virtualisation.docker.enable = true; to configuration.nix" ;;
    void)   printf '%s' "sudo xbps-install -S docker docker-compose" ;;
    gentoo) printf '%s' "sudo emerge app-containers/docker app-containers/docker-compose" ;;
    *)      printf '%s' "see $docs/" ;;
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

# Whether the official convenience script supports this distribution. It goes
# by os-release's ID, not by family: it knows ubuntu, debian, raspbian, centos,
# rhel, rocky and fedora, says "Unsupported distribution" to the rest - SUSE,
# AlmaLinux, Amazon Linux among them - and maps some derivatives to a parent
# release they were not built from. This used to go by family and offered it
# on all of SUSE, where it has never worked.
convenience_script_supported() {
  case "$DISTRO" in
    ubuntu|debian|raspbian|centos|rhel|rocky|fedora) return 0 ;;
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
  info "  1. By hand:"
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
      die "Install Docker as shown above, then run this script again."
    fi
  else
    info ""
    info "  2. Docker's own installer does not support $DISTRO_NAME, so install"
    info "     it as above."
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

# Replace KEY's line with a comment saying why it went, the same way as
# set_env_value. A deleted line would leave somebody reading .env no trace of
# what changed it.
drop_env_value() {
  local key="$1" note="$2" env_file="$INSTALL_DIR/.env" tmp
  grep -q "^${key}=" "$env_file" 2>/dev/null || return 0
  tmp="$(mktemp)" || die "Could not create a temporary file."
  TMP_FILES+=("$tmp")
  awk -v key="$key" -v note="$note" '
    index($0, key "=") == 1 { if (!done) print "# " note; done = 1; next }
    { print }' "$env_file" >"$tmp"
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
  open_models "$root" || { warn "Could not make $root readable to the container."; return 1; }
  ok "All ten files verified in $root"
}

# Readable to the container's UID 10001: the directory itself, and the three
# this script fills - never -R on the answer. "Model directory?" once took ~,
# and `chmod -R a+rX ~` made a home directory, .ssh included, world-readable.
open_models() {
  chmod a+rx "$1" && chmod -R a+rX "$1/asr" "$1/vad" "$1/punc"
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

# The CloakBrowser commit this checkout builds when .env names none: the
# default in its docker/compose.yml, which docker/browser_rpc/requirements.txt
# was verified with. Read from the checkout rather than kept in this script,
# because an upgrade runs one release's script against another release's tree,
# and only the tree knows what it was verified with.
compose_browser_pin() {
  # Single quotes: the ${...} is compose's text being matched, not an expansion.
  # shellcheck disable=SC2016
  sed -n 's/^ *CLOAKBROWSER_COMMIT: \${CLOAKBROWSER_COMMIT:-\([0-9a-f]\{40\}\)}.*$/\1/p' \
    "$INSTALL_DIR/docker/compose.yml" 2>/dev/null | head -n 1
}

# A release from before that default - 5.1.6 and older - builds no browser
# without a commit in .env, and its .env.example names the one it expects.
example_browser_pin() {
  sed -n 's/^CLOAKBROWSER_COMMIT=\([0-9a-f]\{40\}\)$/\1/p' "$INSTALL_DIR/.env.example" 2>/dev/null | head -n 1
}

is_written_pin() {
  case " $CLOAKBROWSER_WRITTEN_PINS " in *" $1 "*) return 0 ;; esac
  return 1
}

# A CloakBrowser revision in .env that this project wrote there, rather than
# one somebody chose, comes out, so the build takes docker/compose.yml's - the
# one the checkout's requirements were verified with. An install from
# September kept 0.5.10 this way, and upgrading it would have built 0.5.10 on
# Playwright 1.64, a pair nobody had run. A revision somebody chose stays: it
# is theirs, but they are told it is not the verified one. Done whether or not
# the browser is on, so turning it on later builds the right one.
settle_browser_pin() {
  local pin default legacy
  pin="$(env_value CLOAKBROWSER_COMMIT)"
  default="$(compose_browser_pin)"
  if [ -z "$default" ]; then
    legacy="$(example_browser_pin)"
    [ -n "$legacy" ] || return 0
    if [ -z "$pin" ] || { is_written_pin "$pin" && [ "$pin" != "$legacy" ]; }; then
      set_env_value CLOAKBROWSER_COMMIT "$legacy"
      ok "CLOAKBROWSER_COMMIT=${legacy:0:12} in .env: this release builds its browser from a commit named there."
    fi
    return 0
  fi
  [ -n "$pin" ] || return 0
  if is_written_pin "$pin"; then
    drop_env_value CLOAKBROWSER_COMMIT \
      "CLOAKBROWSER_COMMIT=$pin taken out by install/$SCRIPT_NAME: unset, it follows docker/compose.yml."
    ok "The browser image follows this checkout's CloakBrowser now (${default:0:12})."
    if [ "$pin" != "$default" ]; then
      info "It was on ${pin:0:12}, which an earlier version wrote into .env."
    fi
  elif [ "$pin" != "$default" ]; then
    warn "CLOAKBROWSER_COMMIT in .env pins ${pin:0:12}; kept, since somebody chose it."
    info "This checkout's browser dependencies were verified with ${default:0:12}."
    info "Delete that line from .env to follow the checkout instead."
  fi
}

# Compose reads this path out of .env into a short volume spec, where a colon
# ends the path and a space or a '#' changes what the line means first.
valid_model_path() {
  valid_path "$1" || return 1
  case "$1" in
    *[!A-Za-z0-9._/~+-]*) warn "Use letters, digits and . _ - + / only in this path."; return 1 ;;
  esac
  local dir
  dir="$(absolute_path "$(expand_tilde "$1")")"
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then
    warn "$dir exists and is not a directory."
    return 1
  fi
  # The models go into asr/, vad/ and punc/ under it, which is the only part
  # of it this script touches. A directory full of something else is somebody's
  # directory - a home, /srv - rather than one for the models.
  if [ -d "$dir" ] && [ ! -d "$dir/asr" ] && [ ! -d "$dir/vad" ] && [ ! -d "$dir/punc" ] \
      && [ -n "$(ls -A "$dir" 2>/dev/null || true)" ]; then
    warn "$dir already holds other files. Use a new or empty directory, such as $dir/funasr-onnx."
    return 1
  fi
  return 0
}

# Where the models are, or where they should go. A directory that already
# holds them is used as it is; anything else is offered a download into it.
choose_model_path() {
  MODELS_TO_FETCH=0
  dim "The models stay on this machine and are mounted read-only. A directory"
  dim "that already holds them is used as it is."
  MODEL_PATH="$(ask_value "Model directory?" "${MODEL_PATH:-$INSTALL_DIR/models/funasr-onnx}" valid_model_path)"
  MODEL_PATH="$(absolute_path "$(expand_tilde "$MODEL_PATH")")"

  if models_present "$MODEL_PATH"; then
    ok "Found the models in $MODEL_PATH."
    if ! models_readable "$MODEL_PATH"; then
      warn "Some of them are closed to other users, and the container reads them as UID 10001."
      if ask_yes_no "Make them readable (a+rX on it and its asr, vad and punc; nothing becomes writable)?" "y"; then
        open_models "$MODEL_PATH" || { warn "chmod failed. Transcription stays off."; WANT_TRANSCRIBER=0; }
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
# Empty unless somebody chose a revision: then docker/compose.yml's applies.
BROWSER_COMMIT="${CLOAKBROWSER_COMMIT:-}"
# Read once, then taken out of the environment, along with the version this
# script pins. Compose interpolation prefers the shell's value to .env's, and
# every dtkctl this script runs inherits its environment: with DTK_BIND_HOST=
# 0.0.0.0 in the shell, the answer "keep it on 127.0.0.1" went into .env and
# the stack was published on 0.0.0.0 anyway.
unset DTK_BIND_HOST DTK_BIND_PORT DTK_TRANSCRIBER_MODEL_PATH CLOAKBROWSER_COMMIT DTK_IMAGE_TAG

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
  INSTALL_DIR="$(absolute_path "$(expand_tilde "$INSTALL_DIR")")"

  case "$INSTALL_DIR" in
    ""|"/"|"/usr"|"/etc"|"/var"|"/bin"|"/sbin"|"/lib"|"/boot"|"/home"|"/root")
      die "Refusing to install into $INSTALL_DIR." ;;
  esac
}

# Volumes left by an install of the same name that this one would land on.
# Compose names them after the project, so a new install starts against that
# old database - with new passwords it does not know - and dies at the first
# migration. It used to get that far: an install stopped with `down`, or one
# whose directory was deleted, has no containers for anything to find.
refuse_foreign_volumes() {
  [ -f "$INSTALL_DIR/.env" ] && return 0
  local volumes
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null || true)"
  [ -n "$volumes" ] || return 0
  die "Docker already holds volumes for a project named '$PROJECT':
$(printf '%s\n' "$volumes" | sed 's/^/      /')
    They belong to an install that was stopped or removed without them, and
    their database opens only with that install's .env, which $INSTALL_DIR does
    not have. A new install would generate new passwords and fail against them.
      - That install is still somewhere: run this script there with --manage,
        or set DTK_INSTALL_DIR to it.
      - Install alongside it under another name, in another directory:
          DTK_PROJECT=${PROJECT}2 DTK_INSTALL_DIR=\$HOME/${PROJECT}2 bash install.sh
      - Start over. This deletes that data for good:
          docker volume rm $(printf '%s' "$volumes" | tr '\n' ' ')"
}

# Anywhere but GitHub is agreed to before anything is fetched from it, and
# resolve_target already reads the release tags there. A mirror hands over all
# of the code - docker/compose.yml, and so which images run - and every upgrade
# after this one, and nothing here can tell its copy from GitHub's. So it is
# said in full, and a bare Enter changes nothing. Under --yes the variable that
# named it is the answer, as DTK_ENABLE_BROWSER=1 is: somebody set it.
confirm_mirror() {
  [ "$REPO_URL" = "$GITHUB_REPO_URL" ] && return 0
  local default="n"
  [ "$ASSUME_YES" = 1 ] && default="y"
  step "A mirror instead of GitHub"
  warn "This would clone from $REPO_URL, not from GitHub."
  dim "Everything installed comes through it: the code, docker/compose.yml and so"
  dim "which images run, and every upgrade after this one. Nothing here can tell"
  dim "its copy from GitHub's, so a mirror that is compromised, or run by someone"
  dim "you do not know, can hand you other code. Use one you run or trust."
  dim "To go back to GitHub later:"
  dim "  git -C $INSTALL_DIR remote set-url origin $GITHUB_REPO_URL"
  ask_yes_no "Clone from this mirror?" "$default" \
    || die "Nothing was changed. Without DTK_GH_PROXY and DTK_REPO_URL, this clones from GitHub."
}

prepare_directory() {
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR exists and is not a directory."
  fi
  have git || die "git is not installed. Install it with $(git_install_hint), then run this again."
  # Only a clone uses it: an existing checkout fetches from its own origin,
  # whatever DTK_GH_PROXY says.
  [ -d "$INSTALL_DIR/.git" ] || confirm_mirror

  # The release being installed: its tag is checked out, and its version goes
  # into .env as DTK_IMAGE_TAG. main runs ahead of releases, and a clone of it
  # used to start with main's compose.yml around the last release's images.
  resolve_target "${DTK_VERSION:-latest}"

  if [ -d "$INSTALL_DIR/.git" ]; then
    local origin
    origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "")"
    case "$origin" in
      *Douyin_TikTok_Download_API*) : ;;
      *) die "$INSTALL_DIR is a git checkout of something else ($origin).
    Choose an empty directory." ;;
    esac
    [ -f "$INSTALL_DIR/docker/compose.yml" ] \
      || die "$INSTALL_DIR is a checkout of this project from before v5 (it has no docker/compose.yml).
    v5 shares nothing with it. Choose an empty directory."
    if [ -x "$INSTALL_DIR/dtkctl" ] && [ "$(project_of "$INSTALL_DIR/dtkctl")" != "$PROJECT" ]; then
      die "$INSTALL_DIR is the install of project '$(project_of "$INSTALL_DIR/dtkctl")', not '$PROJECT'.
    Its .env and dtkctl are that install's. Choose another directory: DTK_INSTALL_DIR=..."
    fi
    ok "Existing checkout found at $INSTALL_DIR."
    info "Your .env and your Docker volumes are left alone."
    if checkout_is_at "$RELEASE_REF"; then
      ok "It is at $RELEASE_REF already."
    elif ask_yes_no "Move it to $RELEASE_REF, the release being installed?" "y"; then
      checkout_ref "$RELEASE_REF"
      ok "Checked out $RELEASE_REF."
    else
      # Left where it is, the checkout would run $RELEASE_REF's images under
      # another tree's compose.yml - the mismatch moving it is there to prevent.
      die "Then this would run $RELEASE_REF's images on a checkout of something else. Nothing was changed.
    To install another release, name it: DTK_VERSION=5.1.6 bash install.sh"
    fi
    return
  fi

  if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    die "$INSTALL_DIR is not empty and is not an existing install. Choose another directory."
  fi

  step "Fetching $RELEASE_REF"
  local parent
  parent="$(dirname "$INSTALL_DIR")"
  # As this user first: sudo for a parent somebody can create themselves left
  # root-owned directories in their home.
  if [ ! -d "$parent" ] && ! mkdir -p "$parent" 2>/dev/null; then
    ask_yes_no "$parent does not exist and needs root to create. Create it with sudo?" "y" \
      || die "Create $parent yourself, or choose another directory."
    as_root mkdir -p "$parent"
  fi
  if [ ! -w "$parent" ]; then
    ask_yes_no "$parent is not writable. Create $INSTALL_DIR with sudo, owned by you?" "y" \
      || die "Choose a directory you can write to."
    as_root mkdir -p "$INSTALL_DIR"
    as_root chown "$(id -u):$(id -g)" "$INSTALL_DIR"
  fi

  # All of main first, then the release on top of it. The installer of 5.1.6 -
  # which is what `./dtkctl upgrade` runs until a newer release is checked out -
  # moves the checkout by fast-forwarding it to origin/main before handing
  # over, and that needs main's history between the release and its tip: a
  # clone of the tag alone has no origin/main, and a --depth 1 clone of main
  # has the tip without the path to it. Either way that step failed, and the
  # upgrade carried on in the old tree. main's whole history is about 14 MB.
  local said hint="Check the network, or a mirror if you are in mainland China (DTK_GH_PROXY, see --help): $DOCS_URL"
  # A mirror that failed is not helped by the advice to set one.
  [ "$REPO_URL" = "$GITHUB_REPO_URL" ] \
    || hint="That is a mirror: check that it is up and relays this repository, or clone from GitHub without DTK_GH_PROXY and DTK_REPO_URL."
  if ! said="$(git clone --quiet --single-branch --branch main "$REPO_URL" "$INSTALL_DIR" 2>&1)"; then
    die "Could not clone $REPO_URL:
$(printf '%s\n' "$said" | sed 's/^/      /')
    $hint"
  fi
  checkout_ref "$RELEASE_REF"
  ok "Cloned $RELEASE_REF into $INSTALL_DIR"
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
  # Checked with the browser off too: it is written into .env either way, and
  # whoever turns the browser on later would meet it several minutes into a
  # build that Docker rejects.
  if [ -n "$BROWSER_COMMIT" ] && ! valid_commit "$BROWSER_COMMIT"; then
    die "CLOAKBROWSER_COMMIT has to be a full 40-character commit, not '$BROWSER_COMMIT'."
  fi
  if [ "$WANT_BROWSER" = 1 ]; then
    local verified
    verified="$(compose_browser_pin)"
    [ -n "$verified" ] || verified="$(example_browser_pin)"
    if [ -z "$BROWSER_COMMIT" ]; then
      dim "It installs CloakBrowser at commit ${verified:0:12}, the revision this"
      dim "release was verified with, and follows the checkout when you upgrade."
    else
      dim "It installs CloakBrowser at commit ${BROWSER_COMMIT:0:12}, from CLOAKBROWSER_COMMIT."
      if [ "$BROWSER_COMMIT" != "$verified" ]; then
        warn "This release was verified with ${verified:0:12}. Yours goes in .env, and upgrades leave it."
      fi
    fi
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
    reconcile_env
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
    # Only a revision somebody chose. Left out, docker/compose.yml's default
    # applies and moves with upgrades; written here, it would stay behind
    # while the checkout's browser requirements moved on.
    # A release from before that default gets its commit from settle_browser_pin below.
    if [ -n "$BROWSER_COMMIT" ]; then
      printf 'CLOAKBROWSER_COMMIT=%s\n' "$BROWSER_COMMIT"
    elif [ -n "$(compose_browser_pin)" ]; then
      printf '# CLOAKBROWSER_COMMIT= unset: the browser follows docker/compose.yml.\n'
    fi
    if [ "$USE_PUBLISHED" = 1 ]; then
      printf 'DTK_IMAGE=evil0ctal/douyin_tiktok_download_api\n'
      # The release that was checked out, not `latest`: that tag moves, and an
      # install should be able to say which build it runs - the same reason
      # an upgrade pins the version it moves to.
      printf 'DTK_IMAGE_TAG=%s\n' "$RELEASE_TAG"
      printf 'DTK_DOWNLOADER_IMAGE=evil0ctal/douyin_tiktok_download_api-downloader\n'
      printf 'DTK_TRANSCRIBER_IMAGE=%s\n' "$TRANSCRIBER_IMAGE"
    fi
  } >"$env_file"
  umask "$old_umask"
  chmod 600 "$env_file"
  # A release from before compose.yml had a default wants the commit here.
  settle_browser_pin
  ok "Wrote $env_file (owner-readable only)."
}

# An .env from a run that stopped part-way, kept for its secrets, gets the
# answers just given written over the lines they decide. It used to keep its
# own while dtkctl took the new ones: the browser turned on with no address for
# it, downloads on with no downloader URL, and a summary that said 127.0.0.1
# over an .env that published on 0.0.0.0.
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
  # dtkctl is about to start the transcriber, and an .env from before it
  # existed would leave the api without its address and it without a token.
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

# Docker refuses to start a container asking for more CPUs than the host has -
# a hard error, not a slow start - and on a small box the shipped memory
# ceilings add up to more RAM than exists. So a host that is smaller than the
# defaults were written for gets its own override file.
write_host_override() {
  local override="$INSTALL_DIR/compose.host.yml"
  local need_cpu=0 need_mem=0

  [ "$CPUS" -lt 4 ] && need_cpu=1
  [ "$MEM_MIB" -gt 0 ] && [ "$MEM_MIB" -lt 7800 ] && need_mem=1

  local previous=""
  if [ -f "$override" ]; then
    previous="$(mktemp)" || die "Could not create a temporary file."
    TMP_FILES+=("$previous")
    cat "$override" >"$previous"
  fi

  if [ "$need_cpu" = 0 ] && [ "$need_mem" = 0 ]; then
    rm -f "$override"
    keep_edited_override "$previous" "$override"
    return
  fi

  step "Sizing for this machine"
  # Every service whose compose.yml ceiling is above what this host has, and
  # never more than the host: Docker refuses a container asking for more CPUs
  # than exist. A fixed 1.5 for the API on any host of two cores or fewer -
  # and migrate and worker left at the 2.0 compose gives them - meant a
  # one-core host could not run even the first migration.
  # api_cpu is api and worker, full_cpu the ones compose gives 2.0 (migrate,
  # transcriber).
  local api_cpu full_cpu browser_cpu
  case "$CPUS" in
    1) api_cpu="1.0"; full_cpu="1.0"; browser_cpu="0.5" ;;
    # Leave at least half a core: a browser pinned to every core starves the
    # API that has to answer while it mints.
    2) api_cpu="1.5"; full_cpu="2.0"; browser_cpu="1.5" ;;
    3) api_cpu="2.0"; full_cpu="2.0"; browser_cpu="3.0" ;;
    *) api_cpu="2.0"; full_cpu="2.0"; browser_cpu="4.0" ;;
  esac

  local redis_mib=$(( MEM_MIB / 12 )) app_mib=$(( MEM_MIB / 9 )) dl_mib=$(( MEM_MIB / 20 ))
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
      # memswap_limit goes with every mem_limit whose service has one in
      # compose.yml. Docker refuses a container whose memswap_limit is below
      # its mem_limit, and the 512m api and worker inherit was below MEM/9 on
      # every host between about 4.6 and 7.6 GiB.
      printf '  postgres:\n    mem_limit: %sm\n' "$(( MEM_MIB / 4 ))"
      printf '  redis:\n    mem_limit: %sm\n' "$redis_mib"
      printf '  migrate:\n    cpus: %s\n' "$full_cpu"
      printf '  api:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: %s\n' "$app_mib" "$app_mib" "$api_cpu"
      printf '  worker:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: %s\n' "$app_mib" "$app_mib" "$api_cpu"
      printf '  browser-rpc:\n    mem_limit: %sm\n    cpus: %s\n' "$(( MEM_MIB / 3 ))" "$browser_cpu"
      printf '  downloader:\n    mem_limit: %sm\n    memswap_limit: %sm\n    cpus: 1.0\n' "$dl_mib" "$dl_mib"
      # Flat rather than a share of RAM: it needs what the models and one
      # decoded file need (0.9 GiB measured on a 3-minute clip; an hour of
      # audio adds about 230 MB) whatever the host. No swap, as in compose,
      # because inference that pages is slower than inference that fails.
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
  ok "Wrote compose.host.yml — ceilings scaled to $CPUS CPU and $MEM_MIB MiB."
  keep_edited_override "$previous" "$override"

  # Redis evicts at maxmemory and is killed at mem_limit, so the first has to
  # sit well under the second. compose.yml's 320mb does under its 512m; under a
  # limit scaled to a small host it did not, and Redis was OOM-killed - taking
  # the task queue with it - where it should have evicted. A value somebody
  # set stays.
  if [ "$need_mem" = 1 ] && [ $(( redis_mib * 6 / 10 )) -lt 320 ] && [ -z "$(env_value DTK_REDIS_MAXMEMORY)" ]; then
    set_env_value DTK_REDIS_MAXMEMORY "$(( redis_mib * 6 / 10 ))mb"
    ok "Redis evicts at $(( redis_mib * 6 / 10 ))mb, under its ${redis_mib}m ceiling (DTK_REDIS_MAXMEMORY in .env)."
  fi
}

# The file says "edit these freely", so a run that rewrites it keeps what was
# there before when it differs, rather than dropping somebody's edits.
keep_edited_override() {
  local previous="$1" override="$2"
  [ -n "$previous" ] && [ -s "$previous" ] || return 0
  if [ -f "$override" ] && cmp -s "$previous" "$override"; then return 0; fi
  cat "$previous" >"$override.old"
  info "The compose.host.yml that was there is kept as compose.host.yml.old; carry over any edits from it."
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
    printf '#   ./dtkctl upgrade         to the latest release: back up, pull, migrate, restart\n'
    printf '#   ./dtkctl upgrade 5.1.6   to that release instead (--no-backup skips the backup)\n'
    printf '#\n'
    printf '# The three things it carries that are easy to forget: the project name, the\n'
    printf '# host override file, and COMPOSE_ENV_FILES - without which compose looks for\n'
    printf '# docker/.env and silently interpolates defaults.\n'
    printf '#\n'
    printf '# On published images the version is DTK_IMAGE_TAG in .env; upgrade writes it.\n'
    printf 'set -euo pipefail\n'
    # Single quotes on purpose: these expand when dtkctl runs, not now. The
    # loop follows a symlink to dtkctl - one put on PATH - back to the install,
    # where "$0" alone found neither docker/compose.yml nor the installer.
    # shellcheck disable=SC2016
    printf 'here="$0"\nwhile [ -L "$here" ]; do\n  link="$(readlink "$here")"\n'
    # shellcheck disable=SC2016
    printf '  case "$link" in /*) here="$link" ;; *) here="$(dirname "$here")/$link" ;; esac\ndone\n'
    # shellcheck disable=SC2016
    printf 'cd "$(dirname "$here")"\n'
    # Not a compose command, so it shadows nothing. Upgrading used to mean
    # editing DTK_IMAGE_TAG in .env by hand and then remembering the checkout,
    # the pull, the browser build and the migration, in that order. The
    # installer that came with the checkout knows them, and writes the tag.
    # shellcheck disable=SC2016
    printf 'if [ "${1:-}" = upgrade ]; then\n  shift\n'
    # A release from before 5.1.6 has an installer without --upgrade. Said
    # here, with what to run instead, rather than as "Unknown option".
    printf '  if ! grep -q -- "--upgrade)" install/%s; then\n' "$SCRIPT_NAME"
    printf '    printf "%%s\\n" "This release has no upgrade command of its own. Run the current installer:" "  curl -fsSL %s/install/%s -o install.sh && bash install.sh --upgrade" >&2\n' "$REPO_RAW" "$SCRIPT_NAME"
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
  ok "Wrote dtkctl — use it instead of raw docker compose."
}

# Compose's own reading of what was just written, before anything is pulled. A
# project name, port or file it rejects used to surface at `pull`, under a
# message blaming the registry.
validate_compose() {
  local problem
  if ! problem="$("$CTL" config -q 2>&1)"; then
    die "Compose rejects this install's configuration:
$(printf '%s\n' "$problem" | sed 's/^/      /')"
  fi
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

# The checkout this script came with, when the script is a file on disk.
# Piped through `curl | bash` it has no directory, and the "." that stood in
# for one made it the parent of wherever it was run.
own_checkout() {
  local src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 1
  (cd "$(dirname "$src")/.." 2>/dev/null && pwd)
}

# Whether DIR is an install this script may act on, and so run the dtkctl of,
# without being told it is: a file called dtkctl in the directory somebody
# happens to be in is not reason enough to execute it. It has to be a checkout
# of this project, with its compose file, and a dtkctl owned by whoever runs
# this - or root - that nobody else can write. DTK_INSTALL_DIR, and the
# directory dtkctl passes for itself, are taken as said.
is_install_dir() {
  local dir="$1" origin
  [ -n "$dir" ] && [ -d "$dir/.git" ] && [ -f "$dir/docker/compose.yml" ] && [ -x "$dir/dtkctl" ] || return 1
  origin="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
  case "$origin" in *Douyin_TikTok_Download_API*) : ;; *) return 1 ;; esac
  [ -n "$(find "$dir/dtkctl" -maxdepth 0 \( -user "$(id -u)" -o -user 0 \) ! -perm -020 ! -perm -002 2>/dev/null)" ]
}

# Where an existing deployment lives, asked of Docker rather than guessed.
# Every container Compose makes is labelled with the config files it came
# from, and the install directory is the first one's grandparent. Read from the
# labels rather than `compose ls` through python3, which a host without python3
# answered with nothing - and the script then installed over the existing one.
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
  local recorded
  recorded="$(env_value DTK_TRANSCRIBER_MODEL_PATH)"
  [ -z "$recorded" ] || MODEL_PATH="$recorded"
}

# The version the running instance reports. The only one that counts: the
# checkout on disk can be ahead of the image that is actually serving.
running_version() {
  "$CTL" exec -T api dtk --version 2>/dev/null | tr -d '\r' | awk 'NF{print $NF}' | tail -1
}

# A v5 release tag and nothing else: v5.1.6, not V4.1.3. The v4 branch still
# cuts releases, under capital-V tags, and GitHub would call one of those the
# latest; an upgrade that took it would have pulled a v4 image into a v5 stack.
is_release_tag() {
  printf '%s' "$1" | grep -Eq '^v([5-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+$'
}

# Where releases come from: the checkout's own origin once there is one, so a
# mirror stays a mirror.
origin_url() {
  # Its own .git only: `git -C` on an empty directory inside some other work
  # tree answers with that tree's origin.
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null && return 0
  fi
  printf '%s' "$REPO_URL"
}

# The highest release tag on the remote that starts with $1 (v5.1. for "5.1",
# v for any). The fallback for latest_release, and how `5.1` finds 5.1.6.
highest_release_tag() {
  local prefix="${1:-v}" best="" tag
  for tag in $(git ls-remote --tags --refs "$(origin_url)" "refs/tags/${prefix}*" 2>/dev/null \
      | sed -n 's|^[0-9a-f]*[[:space:]]*refs/tags/||p'); do
    is_release_tag "$tag" || continue
    if [ -z "$best" ] || version_is_newer "$tag" "$best"; then best="$tag"; fi
  done
  printf '%s' "$best"
}

# The newest v5 release, as its tag. GitHub's "latest" when it is one - it is
# what a release announcement points at, and it only moves once the images are
# up - and otherwise, or when the API does not answer, the highest tag. No
# python3: hosts without it (Alpine, CoreOS) got an empty answer here and so
# never saw an upgrade. The JSON is read for its one field with sed.
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

# True when $1 is newer than $2. A pre-release suffix makes a version older
# than the same numbers without one, so 5.1.0.dev0 is behind 5.1.0 - the same
# rule web/src/lib/version.ts applies in the console. awk, not python3, for
# the reason latest_release gives.
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

# The shapes resolve_target takes, checked as they are typed: a typo at the
# menu's "Which version?" died inside resolve_target and ended the session.
valid_target() {
  case "$1" in
    latest) return 0 ;;
    sha-*) valid_commit "${1#sha-}" && return 0 ;;
    *) printf '%s' "${1#v}" | grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?$' && return 0 ;;
  esac
  warn "A version like 5.1.6, a series like 5.1, latest, or sha-<40-character commit>."
  return 1
}

# What a target names, in the two forms an upgrade needs: RELEASE_TAG for
# DTK_IMAGE_TAG (5.1.6, sha-<40>) and RELEASE_REF for the checkout (v5.1.6, the
# commit). `latest` and `5.1` resolve to the release they mean, so .env is
# pinned to a version rather than to a tag that moves.
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
        || die "'$target' is not a build. A sha- tag carries the full 40-character commit."
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
        die "'$target' is not a version. Name one like 5.1.6, a series like 5.1, latest, or sha-<40-character commit>."
      fi ;;
  esac
  [ -n "$RELEASE_REF" ] || die "Could not find a release for '$target'. Check the network, or name the version: 5.1.6."
  is_release_tag "$RELEASE_REF" || die "'$target' is not a v5 release."
  RELEASE_TAG="${RELEASE_REF#v}"
}

# The checkout has to be the tree the images were built from. It used to
# follow origin/main whatever was installed, so a release's images ran under
# main's compose.yml, browser code and installer - work docker-publish.yml keeps
# out of `latest` on purpose - and going back to an older release could not
# work at all. A published install checks out its release's tag, detached. One
# that builds from source starts on a release too, and its upgrades follow
# main, because the checkout is what it builds.
# Every failure stops here, before anything else has changed.
checkout_ref() {
  local ref="$1" dir="$INSTALL_DIR" origin
  origin="$(origin_url)"
  case "$ref" in
    main)
      git -C "$dir" fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" \
        || die "Could not fetch main from $origin. Nothing was changed."
      if [ "$(git -C "$dir" symbolic-ref -q --short HEAD 2>/dev/null || true)" != main ]; then
        if git -C "$dir" show-ref --verify --quiet refs/heads/main; then
          git -C "$dir" checkout --quiet main 2>/dev/null || checkout_failed main
        else
          git -C "$dir" checkout --quiet -b main origin/main 2>/dev/null || checkout_failed main
        fi
      fi
      git -C "$dir" merge --ff-only --quiet origin/main 2>/dev/null \
        || die "main in $dir has commits that are not on $origin, so it cannot simply move forward.
    Nothing was changed. See: git -C $dir log --oneline origin/main..main" ;;
    # No --depth on these: in a full clone it would make the repository
    # shallow, and cut the history the fast-forward above depends on.
    v*)
      git -C "$dir" fetch --quiet origin "+refs/tags/$ref:refs/tags/$ref" \
        || die "Could not fetch $ref from $origin. Nothing was changed."
      git -C "$dir" checkout --quiet --detach "refs/tags/$ref" 2>/dev/null || checkout_failed "$ref" ;;
    *)
      git -C "$dir" fetch --quiet origin "$ref" \
        || die "Could not fetch commit $ref from $origin. Nothing was changed."
      git -C "$dir" checkout --quiet --detach "$ref" 2>/dev/null || checkout_failed "$ref" ;;
  esac
  [ -f "$dir/docker/compose.yml" ] || die "$ref has no docker/compose.yml; it is not a v5 tree."
}

checkout_failed() {
  die "The checkout in $INSTALL_DIR has local changes that $1 would overwrite:
$(git -C "$INSTALL_DIR" status --short --untracked-files=no 2>/dev/null | sed 's/^/      /')
    Nothing was changed. Move them into an override file - compose.host.yml is
    one - or set them aside with: git -C $INSTALL_DIR stash"
}

# Whether the checkout already is what $1 names: a tag or a commit.
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

# Read a password without echoing it, twice. The caller pipes it to a command
# on stdin; it is never an argument, because argv is readable by every process
# on the host. Nothing typed, or the terminal gone, cancels (status 1): it used
# to answer "Too short" and ask again, forever - on a closed terminal, as fast
# as the loop could spin. Callers set SECRET_PROMPTED, so that a Ctrl-C in here
# does not leave the terminal with echo off.
read_password_twice() {
  local first second
  [ -n "$TTY_IN" ] || return 1
  while true; do
    printf '    New password (at least 8 characters, empty to cancel): ' >&2
    IFS= read -rs first <"$TTY_IN" || { printf '\n' >&2; return 1; }
    printf '\n' >&2
    [ -n "$first" ] || return 1
    if [ "${#first}" -lt 8 ]; then
      warn "Too short."
      continue
    fi
    printf '    Again: ' >&2
    IFS= read -rs second <"$TTY_IN" || { printf '\n' >&2; return 1; }
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

  # "Up to date" only when that is known. It used to be the answer whenever a
  # latest release could be read - with the API down, or ahead of the release.
  latest="$(latest_release)"
  [ -n "$latest" ] || return 0
  printf '\n'
  if [ -n "$installed" ] && version_is_newer "$latest" "$installed"; then
    warn "$latest is out; this instance is on $installed."
  elif [ "$installed" = "${latest#v}" ]; then
    ok "Up to date with the latest release ($latest)."
  else
    info "The latest release is $latest."
  fi
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

# What an upgrade moves to: UPGRADE_TO for DTK_IMAGE_TAG (5.1.7, sha-<40>;
# empty on a source install) and UPGRADE_REF for the checkout (v5.1.7, the
# commit, or main).
UPGRADE_TO=""
UPGRADE_REF=""
# Where the checkout was, and a copy of .env as it was, for undo_upgrade.
UNDO_SHA=""
UNDO_BRANCH=""
UNDO_ENV=""

# From what somebody asked for - a version, a series, latest, a sha- build, or
# nothing, which is the latest release - to what the upgrade moves to. An
# install that builds from source follows main instead.
plan_upgrade() {
  if [ "$USE_PUBLISHED" = 0 ]; then
    if [ -n "${1:-}" ]; then
      die "This install builds its images from source, so it runs what the checkout holds
    and there is no published version to choose. Run ./dtkctl upgrade on its own."
    fi
    UPGRADE_TO=""
    UPGRADE_REF="main"
    return 0
  fi
  resolve_target "${1:-latest}"
  UPGRADE_TO="$RELEASE_TAG"
  UPGRADE_REF="$RELEASE_REF"
}

# The checkout and .env back the way the upgrade found them. cleanup runs this
# while UPGRADE_UNDO is set - from moving the checkout until the stack is
# swapped - so an upgrade that stops in between, on an error or a Ctrl-C,
# leaves the install as it was. "Nothing was changed" used to be said with the
# checkout already moved to main and .env already pinned to the new version.
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
      warn "Could not put the checkout back where it was. To do it by hand:"
      dim "git -C $INSTALL_DIR checkout ${UNDO_BRANCH:-$UNDO_SHA}"
      return 0
    fi
  fi
  local where
  where="$(git -C "$INSTALL_DIR" describe --tags --exact-match 2>/dev/null || true)"
  warn "Put back: the checkout is at ${UNDO_BRANCH:-${where:-${UNDO_SHA:0:12}}} again and .env is as it was."
  info "The version that was running is still running."
}

# Migrations only run forwards, so the moment before them is the one worth an
# archive. Taken by the api that is running now - the old version - into the
# backup volume. Nothing running means nothing to take it, which is said
# rather than stopping an upgrade that may be what gets it running again.
backup_before_upgrade() {
  if [ "$NO_BACKUP" = 1 ]; then
    info "No backup first (--no-backup)."
    return 0
  fi
  if [ -z "$(running_version || true)" ]; then
    warn "The API is not running, so there is no backup from before this upgrade."
    return 0
  fi
  info "Backing up first, into the backup volume."
  # Single quotes: DTK_BACKUP_DIR expands inside the container, not here.
  # shellcheck disable=SC2016
  if ! "$CTL" exec -T api sh -c 'dtk backup create -o "$DTK_BACKUP_DIR"'; then
    die "The backup failed, so nothing was upgraded. Read its output above, or go
    ahead without one: ./dtkctl upgrade ${UPGRADE_TO:+$UPGRADE_TO }--no-backup"
  fi
}

# Migrations do not run backwards, so a release that does not know the
# migration this database is at cannot run on it. Asked before anything is
# pulled: the running api names the head it migrated to, and the tree just
# checked out either has that migration or does not. Without this the answer
# came at the migrate step, after the pull, the browser build and a backup.
refuse_unknown_schema() {
  local running="$1" head
  head="$("$CTL" exec -T api dtk migrate --show 2>/dev/null | awk '/revision on disk/ {print $NF}' || true)"
  case "$head" in ''|none) return 0 ;; esac
  if ! ls "$INSTALL_DIR/src/dtk/db/migrations/versions/${head}_"*.py >/dev/null 2>&1; then
    die "${UPGRADE_TO:-This checkout} cannot run on this database: it is at migration $head,
    which ${UPGRADE_TO:-this checkout} does not have${running:+ - $running migrated it there}. Migrations do not
    run backwards. The way back is a new install of ${UPGRADE_TO:-it} that keeps this .env - a
    backup opens only with the DTK_SECRET_KEY it was made under - with a backup made on it restored."
  fi
}

# The upgrade to UPGRADE_TO / UPGRADE_REF, in an order that is the point of it:
# the checkout moves first and hands over to the installer that came with it;
# then everything that can fail without touching the running stack - compose's
# reading of the new tree, the pull, the browser build, the backup, the
# migrations - with undo_upgrade armed; only then is the version pinned in .env
# and the stack swapped. Migrations used to run after the pin, so an upgrade to
# an older release failed there and left .env naming it.
do_upgrade() {
  local before
  if [ -z "${DTK_UPGRADE_AT_TARGET:-}" ]; then
    step "Upgrading${UPGRADE_TO:+ to $UPGRADE_TO}"
    info "Your data stays in its volumes."
    printf '\n'
    UNDO_SHA="$(git -C "$INSTALL_DIR" rev-parse -q --verify HEAD 2>/dev/null || true)"
    UNDO_BRANCH="$(git -C "$INSTALL_DIR" symbolic-ref -q --short HEAD 2>/dev/null || true)"
    info "Moving the checkout to $UPGRADE_REF."
    checkout_ref "$UPGRADE_REF"
    UPGRADE_UNDO=1
    # The rest is the new tree's to do, so it is handed to the installer that
    # came with that tree, which knows what its compose file needs. Only to one
    # that speaks this handover: 5.1.6's would move the checkout to main again
    # and 5.1.5's does not know --upgrade at all, so with those this one
    # carries on itself. exec runs no EXIT trap; the next one owns the undo.
    local next="$INSTALL_DIR/install/$SCRIPT_NAME"
    if [ -f "$next" ] && grep -q 'DTK_UPGRADE_AT_TARGET' "$next"; then
      local args=(--upgrade)
      [ -z "$UPGRADE_TO" ] || args+=("$UPGRADE_TO")
      [ "$NO_BACKUP" = 0 ] || args+=(--no-backup)
      info "Handing over to the installer that came with $UPGRADE_REF."
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
  UNDO_ENV="$(mktemp "$INSTALL_DIR/.env.upgrade.XXXXXX")" || die "Could not create a temporary file in $INSTALL_DIR."
  cat "$INSTALL_DIR/.env" >"$UNDO_ENV"
  refuse_unknown_schema "$before"
  with_target_tag validate_compose

  if [ "$USE_PUBLISHED" = 1 ]; then
    info "Pulling."
    local services=(api worker)
    [ "$WANT_DOWNLOADER" = 1 ] && services+=(downloader)
    # Only a published transcriber is pulled. A local one is built by `up`,
    # because the tag it is asked for is new and so not on the machine yet.
    if [ "$WANT_TRANSCRIBER" = 1 ] && [ -n "$(env_value DTK_TRANSCRIBER_IMAGE)" ]; then
      services+=(transcriber)
    fi
    if ! with_target_tag "$CTL" pull "${services[@]}"; then
      die "Could not pull $UPGRADE_TO.
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
    "$CTL" build "${builds[@]}" || die "The images failed to build."
  fi

  # The browser image is built here, never pulled, so an upgrade rebuilds it
  # against the new checkout - with the CloakBrowser commit that tree names.
  settle_browser_pin
  if [ "$WANT_BROWSER" = 1 ]; then
    info "Building the browser image. Cached layers make this quick unless CloakBrowser"
    info "moved, which downloads a new Chromium and takes several minutes."
    with_target_tag "$CTL" build browser-rpc || die "The browser image failed to build."
  fi

  backup_before_upgrade

  info "Migrating."
  # Against the postgres that is running, untouched, when there is one. `run`
  # starts a service's dependencies the way `up` would, and recreates any whose
  # configuration differs in the new tree: a rollback that was then refused
  # still restarted the database on the way.
  # ${deps[@]+...} because bash 3.2, macOS's, calls an empty array unbound
  # under set -u.
  local deps=()
  if "$CTL" ps --status running --services 2>/dev/null | grep -qx postgres; then
    deps=(--no-deps)
  fi
  if ! with_target_tag run_creating_networks "$CTL" run --rm ${deps[@]+"${deps[@]}"} migrate; then
    case "$UPGRADE_TO" in
      [0-9]*)
        if [ -n "$before" ] && version_is_newer "$before" "$UPGRADE_TO"; then
          die "$UPGRADE_TO cannot run on this database: $before has migrated it further,
    and migrations do not run backwards. The way back to $UPGRADE_TO is a new
    install of it that keeps this .env - a backup opens only with the
    DTK_SECRET_KEY it was made under - with a backup made on $UPGRADE_TO restored."
        fi ;;
    esac
    die "Migrations failed. Read the output above. The database may have taken some of
    them; the backup made just before is the way back if it needs one."
  fi

  if [ -n "$UPGRADE_TO" ]; then
    set_env_value DTK_IMAGE_TAG "$UPGRADE_TO"
    ok "Pinned DTK_IMAGE_TAG=$UPGRADE_TO"
  fi
  # Given the upgrade command if it has none, and otherwise left alone.
  # Rewritten on every upgrade, it lost whatever somebody had added to its
  # compose line - an -f for their own override - and the stack came up
  # without it.
  if ! grep -q '= upgrade ]' "$CTL"; then
    write_control_script
  fi
  UPGRADE_UNDO=""
  rm -f "$UNDO_ENV"

  info "Restarting."
  run_creating_networks "$CTL" up -d --wait || die "The new version did not come up healthy. Try: $CTL logs api"
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
  here="$(own_checkout || true)"
  if is_install_dir "$here"; then
    printf '%s' "$here"
  elif is_install_dir "$PWD"; then
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
  # Handed over by the installer that moved the checkout here, which has
  # already decided what this is an upgrade to.
  if [ -n "${DTK_UPGRADE_AT_TARGET:-}" ]; then
    UPGRADE_TO="$UPGRADE_TARGET"
    UPGRADE_REF="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || true)"
    do_upgrade
    return 0
  fi
  plan_upgrade "$UPGRADE_TARGET"
  # 5.1.6's installer hands over too, after moving the checkout to main, with
  # what to move to decided - "the same one again" included.
  if [ -z "${DTK_UPGRADE_HANDOVER:-}" ] && [ -n "$UPGRADE_TO" ]; then
    local installed
    installed="$(running_version || true)"
    # All three, not only the running version: an upgrade stopped half-way
    # used to leave .env or the checkout on another version, and this said
    # "nothing to do" to the run that would have put it right.
    if [ "$installed" = "$UPGRADE_TO" ] && [ "$(env_value DTK_IMAGE_TAG)" = "$UPGRADE_TO" ] \
        && checkout_is_at "$UPGRADE_REF"; then
      ok "Already on $UPGRADE_TO. Nothing to do."
      return 0
    fi
    case "$UPGRADE_TO" in
      [0-9]*)
        if [ -n "$installed" ] && version_is_newer "$installed" "$UPGRADE_TO"; then
          warn "$UPGRADE_TO is older than the $installed that is running."
          info "That works only if it knows every migration this database has had. If it does"
          info "not, the upgrade stops at the migration step and puts everything back."
        fi ;;
    esac
  fi
  do_upgrade
}

act_change_password() {
  step "Change a password"
  "$CTL" exec -T -e COLUMNS=200 api dtk user list 2>/dev/null | sed 's/^/    /' || warn "Could not list accounts."
  printf '\n'
  local who password
  who="$(ask_value "Which account?" "admin")"
  [ -n "$who" ] || return 0
  SECRET_PROMPTED=1
  password="$(read_password_twice)" || { info "Cancelled."; return 0; }
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
  SECRET_PROMPTED=1
  password="$(read_password_twice)" || { info "Cancelled."; return 0; }
  if printf '%s' "$password" | "$CTL" exec -T api dtk user create "$who" --role admin --stdin; then
    ok "Created $who as an administrator."
  else
    warn "That did not work. The name may already be taken."
  fi
  unset password
}

act_list_users() {
  step "Accounts"
  "$CTL" exec -T -e COLUMNS=200 api dtk user list 2>/dev/null | sed 's/^/    /' || warn "Could not list accounts."
}

# Every `dtk ... list` below runs with COLUMNS=200. Without a terminal, rich
# lays tables out at 80 columns and wraps a cell that does not fit - an
# archive name came out as "dtk-backup-2026101" over "8T103850Z.tar.gz", and
# that name is what the restore prompt asks for.
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
    "$CTL" exec -T -e COLUMNS=200 api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null | sed 's/^/    /' || true
  else
    warn "The backup failed. Read the output above."
  fi
}

# What a restore does is fill in: rows the archive has and the database does
# not are added, and nothing already there is overwritten. It cannot take an
# instance back to an earlier state; into a new, empty install it restores the
# whole archive. The prompt used to say it replaced the database, which it has
# never done.
act_restore() {
  step "Restore"
  local listing archive
  # Single quotes: DTK_BACKUP_DIR expands inside the container, not here.
  # shellcheck disable=SC2016
  listing="$("$CTL" exec -T -e COLUMNS=200 api sh -c 'dtk backup list --dir "$DTK_BACKUP_DIR"' 2>/dev/null || true)"
  if ! printf '%s' "$listing" | grep -q 'dtk-backup-'; then
    warn "No archives in the backup volume."
    return 0
  fi
  printf '%s\n' "$listing" | sed 's/^/    /'
  printf '\n'
  dim "A restore adds what the archive has and this database lacks. Nothing already"
  dim "here is overwritten, so it does not roll the instance back."
  archive="$(ask_value "Archive (a name from the list, or a path in the container)?" "")"
  [ -n "$archive" ] || return 0
  confirm_word "restore" || { info "Cancelled."; return 0; }
  # The list prints names, and the CLI resolves a bare name against its own
  # working directory, not the backup volume: answering with a name as the
  # prompt asked failed with "no such file". The name is passed as an
  # argument to sh, never spliced into the command.
  # shellcheck disable=SC2016
  "$CTL" exec -T api sh -c 'case "$1" in */*) p="$1" ;; *) p="$DTK_BACKUP_DIR/$1" ;; esac; exec dtk backup restore "$p" --yes' sh "$archive" \
    || warn "The restore failed. Read the output above."
}

act_diagnose() {
  step "Self check"
  local status=0
  "$CTL" exec -T api dtk diagnose 2>&1 | sed 's/^/    /' || status=$?
  # 1 is the report saying something failed, which it has just printed.
  case "$status" in
    0) : ;;
    1) warn "Some checks failed; the report above says which." ;;
    *) warn "The self check could not run. Is the stack up? $CTL ps" ;;
  esac
}

# Per service, so each gets its share. One --tail across the stack cut to 80
# lines in total, which on a busy install were postgres, redis and worker, and
# never a line from the api.
act_logs() {
  step "Logs"
  dim "The last 20 lines of each service. Follow one live with: $CTL logs -f api"
  local service
  for service in $("$CTL" ps --services 2>/dev/null || true); do
    printf '\n'
    info "$service"
    "$CTL" logs --no-log-prefix --tail 20 "$service" 2>&1 | sed 's/^/      /' || true
  done
}

# `up -d` first, which recreates whatever .env or the compose files changed
# under - `restart` alone keeps a container's old environment - and then a
# restart of the application services. Not postgres or redis, which a plain
# `restart` took down along with everything else.
act_restart() {
  step "Restart"
  run_creating_networks "$CTL" up -d --wait || { warn "The stack did not come up healthy. Try: $CTL logs"; return 0; }
  local service apps=()
  for service in $("$CTL" ps --services 2>/dev/null || true); do
    case "$service" in postgres|redis) : ;; *) apps+=("$service") ;; esac
  done
  if [ "${#apps[@]}" -gt 0 ]; then
    "$CTL" restart "${apps[@]}" || warn "Restart failed."
  fi
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
    # All of it, and never `| head`: the list runs to hundreds of lines, head
    # stopped reading at 80, and the SIGPIPE that sent the listing ended the
    # whole script under pipefail, without a word.
    "$CTL" exec -T -e COLUMNS=200 api dtk config list 2>&1 | sed 's/^/    /' || true
    return 0
  fi
  "$CTL" exec -T api dtk config get "$key" 2>&1 | sed 's/^/    /' || { warn "No such setting."; return 0; }
  printf '\n'
  value="$(ask_line "New value (empty to leave it)?")"
  [ -n "$value" ] || return 0
  ask_yes_no "Set $key to that?" "n" || { info "Left as it was."; return 0; }
  # The value on stdin, not argv: argv is in the host's process list for as
  # long as `docker exec` runs, and some of these are credentials - the webhook
  # secret, tokens inside notify.channels. --yes because it was just asked
  # here: the CLI's own question for a sensitive key had no newline, so the
  # sed below held it back and the menu looked hung.
  if "$CTL" exec -T api dtk config set --help 2>/dev/null | grep -q -- '--stdin'; then
    printf '%s\n' "$value" | "$CTL" exec -T api dtk config set "$key" --stdin --yes 2>&1 | sed 's/^/    /' \
      || warn "Rejected. The validator says why above."
  else
    # A release from before --stdin. The value goes on argv, as it always
    # did there; upgrading moves it to stdin.
    "$CTL" exec -T api dtk config set "$key" "$value" --yes 2>&1 | sed 's/^/    /' \
      || warn "Rejected. The validator says why above."
  fi
  unset value
}

# Images of this install that nothing uses, one "ref id" per line.
stale_images() {
  # In use means referenced by a container that exists, running or stopped -
  # not "matches the tag in .env". The .env of a build-from-source install has
  # no tag at all, and with that as the filter this offered to delete the image
  # the stack was serving from. Docker would have refused while it was up, and
  # obliged the moment it was down.
  #
  # Also kept: every image this install's configuration names, whether or not
  # a container exists for it - a stack stopped with `down` has none, and its
  # 6 GB browser image would otherwise go. And images this machine builds are
  # only offered when Compose labelled them as built for this project: those
  # names do not depend on the project, so another install's build looked like
  # this one's. Published images are offered when nothing uses them; they can
  # be pulled again.
  local in_use configured
  in_use="$(docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)"
  configured="$("$CTL" config --images 2>/dev/null | sort -u || true)"
  # IFS here is newline and tab, so `read ref id` needs telling that the two
  # fields are separated by a space. Without it the whole line went into ref,
  # nothing ever matched, and every image counted as unused.
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

  local images
  images="$(stale_images)"

  if [ -n "$images" ]; then
    info "Images from this install that nothing uses:"
    printf '%s\n' "$images" | sed 's/^/      /'
    printf '\n'
    if ask_yes_no "Remove them?" "y"; then
      printf '%s\n' "$images" | while IFS=' ' read -r ref id; do
        docker rmi "$ref" >/dev/null 2>&1 && info "removed $ref" || true
      done
    fi
  else
    ok "No unused images from this install."
  fi

  printf '\n'
  dim "Build cache speeds up rebuilding the browser image enormously - it is why"
  dim "a rebuild after a small change takes seconds. Clearing it costs that."
  dim "It is Docker's, shared by everything this machine builds, not only this install."
  if ask_yes_no "Clear Docker's build cache for the whole machine?" "n"; then
    docker builder prune -af 2>&1 | tail -2 | sed 's/^/    /' || true
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
    # Switched back off rather than left on: an unhealthy transcriber fails
    # every later `up -d --wait`, the last step of an upgrade included, and
    # that one runs after the migrations.
    warn "It did not come up healthy, so transcription goes back off. The reason is"
    warn "usually in the transcriber's log:"
    "$CTL" logs --no-log-prefix --tail 15 transcriber 2>&1 | sed 's/^/      /' || true
    "$CTL" rm -sf transcriber >/dev/null 2>&1 || true
    set_env_value DTK_TRANSCRIBER_URL ""
    set_ctl_profile transcriber off
    WANT_TRANSCRIBER=0
    "$CTL" up -d --wait || warn "The stack did not come back healthy either. Try: $CTL logs"
    return 0
  fi
  ok "Transcription is on. Open a stored video in the console and choose Transcribe."
}

# ----------------------------------------------------------------- menus --

# Returns 0 once something was done - the caller then leaves - and 1 for Back.
menu_uninstall() {
  while true; do
    step "Stop or remove"
    info "  1  Stop the stack, keep every byte of data"
    info "  2  Stop and delete the data volumes - database, media, backups"
    info "  3  As 2, and remove $INSTALL_DIR as well"
    info "  b  Back"
    case "$(ask_choice "Which?")" in
      # `stop`, not `down`: down removes the containers, and the containers'
      # labels are how this script finds an install. After a down, the next
      # run found nothing and set about installing over it.
      1) "$CTL" stop && ok "Stopped. Start it again with: $CTL up -d" ; return 0 ;;
      2)
        warn "This deletes the database, the media volume and every backup in it."
        dim "To keep the backups, copy them out first: $CTL cp api:/var/lib/dtk/backups ./backups"
        confirm_word "delete" || { info "Cancelled."; continue; }
        "$CTL" down -v --remove-orphans || die "Removing the stack failed; see above. Nothing else was deleted."
        ok "Stopped and the volumes are gone."
        return 0 ;;
      3)
        warn "This deletes the volumes AND $INSTALL_DIR, including your .env."
        warn "Without that .env the data could not be decrypted anyway."
        dim "To keep the backups, copy them out first: $CTL cp api:/var/lib/dtk/backups ~/dtk-backups"
        confirm_word "delete" || { info "Cancelled."; continue; }
        # Stops here when that fails: removing the directory after a failed
        # `down -v` left volumes and containers behind with the only .env
        # that opens them gone.
        "$CTL" down -v --remove-orphans || die "Removing the stack failed; see above. $INSTALL_DIR was left in place."
        # Guarded rather than trusting the variable: this is the one place the
        # script removes a tree, and an empty or silly value must not reach rm.
        case "$INSTALL_DIR" in
          ""|"/"|"/usr"|"/etc"|"/var"|"/home"|"/root"|"/opt")
            die "Refusing to remove $INSTALL_DIR." ;;
        esac
        [ -f "$INSTALL_DIR/docker/compose.yml" ] || die "$INSTALL_DIR does not look like an install; not removing it."
        rm -rf -- "$INSTALL_DIR" && ok "Removed $INSTALL_DIR."
        return 0 ;;
      b|"") return 1 ;;
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
        current="$(env_value DTK_IMAGE_TAG)"
        if [ "$USE_PUBLISHED" = 0 ]; then
          # Built from source, so it runs what the checkout holds. A tag typed
          # here used to be pulled from Docker Hub as dtk-app, which fails.
          plan_upgrade ""
          do_upgrade
          pause_for_reader
          continue
        fi
        if [ -n "$upgrade_line" ]; then
          target="$(ask_value "Which version?" "${upgrade_line#v}" valid_target)"
        else
          # No newer release does not mean nowhere to go. An instance pinned to
          # a sha- build - which is what the README tells you to do in
          # production - sits here permanently otherwise: `latest` follows
          # releases, and a release only appears when somebody cuts one. The
          # same field takes an older version, which works as far back as the
          # last one with no migration this database lacks.
          dim "No newer release. You can still move to another published one: a"
          dim "version such as 5.1.6, a sha-… build, or latest."
          target="$(ask_value "Which version?" "${current:-latest}" valid_target)"
        fi
        [ -n "$target" ] || continue
        plan_upgrade "$target"
        if [ "$UPGRADE_TO" = "$current" ]; then
          info "Already on $UPGRADE_TO. Pulling, migrating and restarting anyway."
        fi
        do_upgrade
        pause_for_reader ;;
      3) menu_manage ;;
      4) if menu_uninstall; then return 0; fi ;;
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
                 the latest release, or to VERSION (5.1.6, 5.1, latest or
                 sha-<commit>). ./dtkctl upgrade runs this. A backup is taken
                 before the migrations, and a failure before the stack is
                 swapped puts the checkout and .env back.
  --no-backup    With --upgrade: skip that backup.
  --help, -h     This text.

Run it with no options and it works out which job it is doing: it installs when
there is nothing here, and opens a menu when there is - status, upgrade,
passwords, backups, settings, disk.

Environment (each one presets an answer, so --yes becomes unattended):
  DTK_PROJECT            Compose project name. Default: dtk.
  DTK_INSTALL_DIR        Where to install. Default: /opt/dtk as root, ~/dtk otherwise.
  DTK_REPO_URL           Where to clone from, for a mirror. Default: GitHub.
  DTK_GH_PROXY           An https:// mirror prefix for the GitHub address it
                         clones; off by default. Either one hands the mirror
                         the whole install, so it asks before cloning.
  DTK_VERSION            The release to install: 5.1.6, 5.1, latest (default)
                         or sha-<commit>. Its tag is what gets checked out.
  DTK_BIND_HOST          127.0.0.1 (default) or 0.0.0.0.
  DTK_BIND_PORT          Default: 8000.
  DTK_ENABLE_BROWSER     1 to build the browser container, 0 (default) not to.
  DTK_ENABLE_DOWNLOADER  1 (default) for media downloads, 0 without.
  DTK_ENABLE_TRANSCRIBER 1 for offline transcription, 0 (default) without.
                         Needs media downloads and two cores.
  DTK_TRANSCRIBER_MODEL_PATH
                         Where its models are, or are to be downloaded to.
                         Default: <install directory>/models/funasr-onnx.
  CLOAKBROWSER_COMMIT    Pins the CloakBrowser commit the browser image installs.
                         Default: unset, which follows the release's
                         docker/compose.yml and moves with upgrades.
  DTK_USE_PUBLISHED      1 (default) to pull images, 0 to build from source.
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
      -*)         die "Unknown option: $1  (try --help)" ;;
      # The version --upgrade may take, wherever it comes: `upgrade 5.1.6
      # --no-backup` and `upgrade --no-backup 5.1.6` are the same request.
      *)
        if [ "$UPGRADE_ONLY" = 1 ] && [ -z "$UPGRADE_TARGET" ]; then
          UPGRADE_TARGET="$1"
        else
          die "Unexpected argument: $1  (try --help)"
        fi ;;
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
      warn "Docker is not installed."
      if convenience_script_supported; then
        info "A real run would offer to run Docker's own installer for you."
      fi
      info "How to install it on this system:"
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
  detect_docker_resources

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
  # than an installer. Looked for every time - with --yes and with
  # DTK_INSTALL_DIR too, which used to skip the search and install over it:
  # in DTK_INSTALL_DIR, from the labels Docker put on its containers (so it is
  # found wherever it was put), and in the directory this script would suggest.
  # The suggested directory only counts when it holds this project: it is
  # where DTK_PROJECT=dtk2 would otherwise find dtk's install and open its menu.
  local found="" preset=""
  if [ -n "${DTK_INSTALL_DIR:-}" ]; then
    preset="$(absolute_path "$(expand_tilde "$DTK_INSTALL_DIR")")"
    [ -x "$preset/dtkctl" ] && found="$preset"
  fi
  if [ -z "$found" ] && [ -z "$preset" ]; then
    # The checkout this script came with or the directory it runs in, when
    # that is an install: `bash install/install.sh --manage` inside one manages
    # that one, whatever its project is called - the hint this script prints
    # for it said exactly that, and with a project other than dtk it found
    # nothing.
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
    die "No install found for project '$PROJECT'.
    Run this script without --manage to create one, or set DTK_INSTALL_DIR
    to where it lives."
  fi
  if [ -n "$found" ]; then
    INSTALL_DIR="$found"
    CTL="$found/dtkctl"
    PROJECT="$(project_of "$CTL")"
    [ -n "$PROJECT" ] || die "Could not read the project name from $CTL."
    load_install_state
    if [ -z "$TTY_IN" ] || { [ "$ASSUME_YES" = 1 ] && [ "$MANAGE_ONLY" = 0 ]; }; then
      ok "An install is already at $INSTALL_DIR; nothing was changed."
      info "Upgrade it with: $CTL upgrade"
      info "Manage it from a terminal with: bash $INSTALL_DIR/install/$SCRIPT_NAME --manage"
      exit 0
    fi
    # The menu asks for every decision. --yes would answer them unseen: "Remove
    # them?" in Free disk space went ahead, while Restore quietly did nothing.
    ASSUME_YES=0
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
  refuse_foreign_volumes
  prepare_directory
  ask_questions
  fetch_models
  write_env
  write_host_override
  write_control_script
  CTL="$INSTALL_DIR/dtkctl"
  validate_compose
  bring_up
  show_result
}

main "$@"
