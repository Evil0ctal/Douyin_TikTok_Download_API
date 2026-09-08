#!/bin/sh
# Bridge the browser in the image to the HOME the service runs with.
#
# CloakBrowser resolves its binary from $HOME/.cloakbrowser and reads no
# environment variable of its own, so HOME is the only lever. The rootfs is
# read-only, which makes HOME the /tmp tmpfs - fresh on every start and unable
# to hold a browser - while the browser itself lives in the image at
# CLOAKBROWSER_HOME. Linking the versioned directory across gives the tool the
# layout it expects and leaves its small metadata writes on the tmpfs.
set -eu

if [ -d "${CLOAKBROWSER_HOME:-}/.cloakbrowser" ]; then
    mkdir -p "${HOME}/.cloakbrowser"
    for installed in "${CLOAKBROWSER_HOME}"/.cloakbrowser/chromium-*; do
        [ -d "${installed}" ] || continue
        ln -sfn "${installed}" "${HOME}/.cloakbrowser/$(basename "${installed}")"
    done
fi

exec python -m browser_rpc "$@"
