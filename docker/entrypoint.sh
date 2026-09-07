#!/bin/sh
# Entrypoint for the api, worker and migrate containers.
#
# One image, three roles. The role is the first argument; everything after it is
# passed through to the process being started.
#
#   dtk-entrypoint api      [uvicorn options...]
#   dtk-entrypoint worker   [worker options...]
#   dtk-entrypoint migrate  [revision]          (default: head)
#
# The startup gate is DTK_SECRET_KEY. It is the master key that encrypts every
# stored cookie and proxy credential, so the image ships no default: a key that
# is identical on every install is the same as no encryption at all
# (docs/design/08-security.md). Refusing here, before anything connects to a
# database, keeps a half-initialized deployment from ever existing.

set -eu

MIN_SECRET_LEN=32
PROGRAM="dtk"

# Candidate worker entry points, tried in order. The worker is a module rather
# than a console script so the container does not depend on the CLI shim being
# installed; DTK_WORKER_COMMAND overrides the whole thing.
WORKER_MODULES="dtk.worker dtk.ops.worker"

log() {
    echo "${PROGRAM}: $*" >&2
}

die() {
    echo "${PROGRAM}: refusing to start: $1" >&2
    shift
    for line in "$@"; do
        if [ -z "${line}" ]; then
            echo "" >&2
        else
            echo "  ${line}" >&2
        fi
    done
    exit 78 # EX_CONFIG: the configuration is wrong, retrying will not help.
}

usage() {
    cat >&2 <<'USAGE'
dtk container entrypoint.

Usage:
  api      [uvicorn options...]   Serve the REST API, MCP and the console
  worker   [worker options...]    Run the crawl worker loop
  migrate  [revision]             Apply database migrations, then exit (default: head)

Every role requires DTK_SECRET_KEY (at least 32 characters).
USAGE
}

require_secret_key() {
    key="${DTK_SECRET_KEY:-}"
    if [ -z "${key}" ]; then
        die "DTK_SECRET_KEY is not set." \
            "It is the master key for every stored cookie and proxy credential." \
            "The image ships no default, because a shared default is not encryption." \
            "Generate one and put it in .env at the repository root:" \
            "" \
            "    echo \"DTK_SECRET_KEY=\$(openssl rand -base64 48)\" >> .env" \
            ""
    fi
    if [ "${#key}" -lt "${MIN_SECRET_LEN}" ]; then
        die "DTK_SECRET_KEY is ${#key} characters; at least ${MIN_SECRET_LEN} are required." \
            "Replace it in .env with a freshly generated key:" \
            "" \
            "    openssl rand -base64 48" \
            "" \
            "Changing the key makes already-stored credentials undecryptable;" \
            "retire the affected identities and mint or import them again." \
            ""
    fi
}

run_api() {
    # The container listens on all of its own interfaces; what limits exposure
    # is the published port, which compose binds to 127.0.0.1 by default
    # (docs/design/08-security.md).
    host="${DTK_BIND_HOST:-0.0.0.0}"
    port="${DTK_BIND_PORT:-8000}"

    set -- --host "${host}" --port "${port}" --no-server-header "$@"
    if [ -n "${DTK_FORWARDED_ALLOW_IPS:-}" ]; then
        # Only with a reverse proxy in front. Without one, forwarded headers are
        # attacker-controlled, which is why this is opt-in.
        set -- --proxy-headers --forwarded-allow-ips "${DTK_FORWARDED_ALLOW_IPS}" "$@"
    fi

    log "starting api on ${host}:${port}"
    exec python -m uvicorn dtk.api.app:create_app --factory "$@"
}

run_worker() {
    if [ -n "${DTK_WORKER_COMMAND:-}" ]; then
        log "starting worker: ${DTK_WORKER_COMMAND}"
        # Deliberate word splitting: the override is a command line, not a path.
        # shellcheck disable=SC2086
        exec ${DTK_WORKER_COMMAND} "$@"
    fi

    for module in ${WORKER_MODULES}; do
        if python -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('${module}') else 1)" \
                2>/dev/null; then
            log "starting worker: python -m ${module}"
            exec python -m "${module}" "$@"
        fi
    done

    die "no worker entry point in this image." \
        "None of these is importable: ${WORKER_MODULES}." \
        "Either the image was built from a tree without the worker module, or" \
        "this deployment starts it differently. Name the command explicitly:" \
        "" \
        "    DTK_WORKER_COMMAND=\"python -m your.worker.module\"" \
        ""
}

run_migrate() {
    revision="${1:-head}"
    log "applying migrations up to ${revision}"
    # Driven through dtk.db.migrate rather than the alembic CLI so the revision
    # directory is found inside the installed package; alembic.ini is not in the
    # image and the working directory is not assumed.
    exec python -c 'import sys
from dtk.db.migrate import upgrade
upgrade(revision=sys.argv[1])' "${revision}"
}

role="${1:-api}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "${role}" in
    api)
        require_secret_key
        run_api "$@"
        ;;
    worker)
        require_secret_key
        run_worker "$@"
        ;;
    migrate)
        # The key is checked here too: a schema created for a deployment that
        # cannot decrypt its own credentials is worse than a failed migration,
        # and this failure arrives before any table exists.
        require_secret_key
        run_migrate "$@"
        ;;
    help | -h | --help)
        usage
        exit 0
        ;;
    *)
        log "unknown role: ${role}"
        usage
        exit 64 # EX_USAGE
        ;;
esac
