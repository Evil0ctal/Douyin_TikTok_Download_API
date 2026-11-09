#!/bin/sh
# Points apt and pip at the package mirrors named by APT_MIRROR and PYPI_MIRROR,
# for an image built on a host that reaches Debian's or PyPI's own servers
# slowly or not at all (issue 817). Run once, as root, before the first
# `apt-get` and `pip install`. Both unset - the default - changes nothing.
#
#   APT_MIRROR   a Debian mirror's base address, the one that holds debian/
#                and debian-security/, e.g. https://mirrors.tuna.tsinghua.edu.cn
#   PYPI_MIRROR  a PyPI simple index, e.g. https://pypi.tuna.tsinghua.edu.cn/simple
#
# The two are not equally safe, which is why they are checked differently. apt
# verifies Debian's signature against the keyring already in the image whoever
# serves the files, so a mirror - even over plain http, which is Debian's own
# default - can withhold packages but not substitute them. pip has no such check
# here: the requirements files pin versions, not hashes, so the index is trusted
# with the code it serves, and plaintext would extend that trust to anyone on
# the path. https only.
#
# Named for what they are rather than after a tool's own variable: they live in
# .env, which is also every container's env_file, and a PIP_INDEX_URL there
# would follow the stack into its runtime. pip is configured through
# /etc/pip.conf instead, which every later `pip install` in the build reads
# without repeating anything.
#
# The optional argument is the root to apply them under, so that the tests can
# run this against a scratch directory (tests/unit/test_package_mirrors.py).
set -eu

# The character checks below are byte ranges; a UTF-8 locale is free to read
# them otherwise.
LC_ALL=C
export LC_ALL

root="${1:-/}"
root="${root%/}"
apt_mirror="${APT_MIRROR:-}"
pypi_mirror="${PYPI_MIRROR:-}"

fail() {
  echo "package-mirrors: $*" >&2
  exit 1
}

# Every check before any write: half a mirror set is a confusing build.
if [ -n "$apt_mirror" ]; then
  case "$apt_mirror" in
    http://* | https://*) ;;
    *) fail "APT_MIRROR must start with http:// or https://, got '$apt_mirror'" ;;
  esac
  # The address becomes a sed replacement. A character outside this set could
  # end it early or be read as its syntax, and some other address would be
  # written in its place.
  case "$apt_mirror" in
    *[!A-Za-z0-9.:/_~-]*)
      fail "APT_MIRROR may contain only letters, digits and . : / _ ~ -, got '$apt_mirror'"
      ;;
  esac
  apt_mirror="${apt_mirror%/}"
fi

if [ -n "$pypi_mirror" ]; then
  case "$pypi_mirror" in
    https://*) ;;
    *)
      fail "PYPI_MIRROR must start with https:// - the requirements pin versions, not" \
        "hashes, so a plaintext index lets anyone on the path swap a package; got '$pypi_mirror'"
      ;;
  esac
  case "$pypi_mirror" in
    *[!A-Za-z0-9.:/_~%-]*)
      fail "PYPI_MIRROR may contain only letters, digits and . : / _ ~ % -, got '$pypi_mirror'"
      ;;
  esac
fi

if [ -n "$apt_mirror" ]; then
  rewritten=0
  for file in "$root/etc/apt/sources.list" \
    "$root"/etc/apt/sources.list.d/*.list \
    "$root"/etc/apt/sources.list.d/*.sources; do
    [ -f "$file" ] || continue
    grep -Eq 'https?://(deb|security)\.debian\.org' "$file" || continue
    # Through a temporary file rather than `sed -i`, whose spelling differs
    # between GNU and BSD sed; `cat >` keeps the file's owner and mode.
    sed -E "s#https?://(deb|security)\\.debian\\.org#${apt_mirror}#g" "$file" >"$file.mirror"
    cat "$file.mirror" >"$file"
    rm -f "$file.mirror"
    rewritten=$((rewritten + 1))
    echo "package-mirrors: ${file#"$root"} now points at $apt_mirror"
  done
  # Accepting the setting and changing nothing would leave the build timing out
  # against deb.debian.org with the mirror apparently set - the same silent
  # failure as editing the wrong sources file on Ubuntu 24.04.
  if [ "$rewritten" -eq 0 ]; then
    fail "APT_MIRROR is set, but no apt source under $root/etc/apt names" \
      "deb.debian.org or security.debian.org to replace"
  fi
fi

if [ -n "$pypi_mirror" ]; then
  mkdir -p "$root/etc"
  printf '[global]\nindex-url = %s\n' "$pypi_mirror" >"$root/etc/pip.conf"
  echo "package-mirrors: pip now installs from $pypi_mirror"
fi
