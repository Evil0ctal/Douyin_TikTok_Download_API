# syntax=docker/dockerfile:1
#
# The api and worker images are the same image. They differ only in the verb
# handed to the entrypoint, because two images built from one source tree drift
# apart the first time somebody rebuilds one of them (docs/design/09-deployment.md).
#
# Three stages:
#   console  builds the React console with npm, so node never reaches runtime
#   deps     resolves Python dependencies with uv into a self-contained venv
#   runtime  carries the venv, the installed package and the built console
#
# The runtime image holds no compiler, no package manager, no default password
# and no writable application directory. It runs as an unprivileged user and is
# expected to be started with a read-only root filesystem plus a tmpfs on /tmp
# (docs/design/08-security.md).

ARG PYTHON_VERSION=3.12.8
ARG NODE_VERSION=22
# uv is pinned: the lockfile it resolves is the reproducibility guarantee, and a
# floating installer would quietly undo that.
ARG UV_VERSION=0.9.18
ARG PYTHON_BASE=python:${PYTHON_VERSION}-slim-bookworm


# --------------------------------------------------------------------------
# Stage 1: the console
# --------------------------------------------------------------------------
# `--platform=$BUILDPLATFORM`: this stage emits JavaScript, CSS and HTML, none of
# which is architecture-specific, so it is built once on the runner's own
# architecture and its output copied into every target image. Without it a
# two-platform build runs `npm ci` and vite twice, once under QEMU emulation,
# which is by far the slowest thing in the whole build for no gain at all.
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-alpine AS console

WORKDIR /build

# Manifest and lockfile first: this layer is reused across every source-only
# change, and `npm ci` refuses to run without the lockfile, which is what keeps
# the console build reproducible.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./

# The output directory is a contract with the runtime stage. `test -d` turns a
# vite config that writes somewhere else into a build failure instead of an
# image that serves nothing.
ARG CONSOLE_OUT_DIR=dist
RUN npm run build && test -d "${CONSOLE_OUT_DIR}" && mv "${CONSOLE_OUT_DIR}" /console


# --------------------------------------------------------------------------
# Stage 2: Python dependencies
# --------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${PYTHON_BASE} AS deps

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies before source: editing a Python file must not re-resolve or
# re-download the dependency set.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
# --no-editable installs a real copy into site-packages, so the runtime image
# does not need /app/src on disk and nothing in the image points at a build path.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# --------------------------------------------------------------------------
# Stage 3: runtime
# --------------------------------------------------------------------------
FROM ${PYTHON_BASE} AS runtime

ARG DTK_UID=10001
ARG DTK_GID=10001

RUN groupadd --system --gid "${DTK_GID}" dtk \
 && useradd --system --uid "${DTK_UID}" --gid "${DTK_GID}" \
            --home-dir /app --shell /usr/sbin/nologin dtk

# HOME and the cache directory point at /tmp because the root filesystem is
# mounted read-only in production; /tmp is the one tmpfs the containers get.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    DTK_CONSOLE_DIR=/app/web/dist

WORKDIR /app

# Everything stays root-owned and read-only for the runtime user: the process
# has no business rewriting its own code, and the read-only rootfs then costs
# nothing.
COPY --from=deps --chown=root:root /app/.venv /app/.venv
COPY --from=console --chown=root:root /console /app/web/dist
COPY --chown=root:root --chmod=0555 docker/entrypoint.sh /usr/local/bin/dtk-entrypoint

# Backups are the one thing the runtime writes. The directory has to exist in
# the image and be owned by the runtime user: Docker seeds a named volume from
# the image path it covers, so an absent or root-owned path yields a root-owned
# volume that a non-root process cannot write - which is a permission error at
# the moment someone asks for a backup, not at startup.
RUN install -d -o dtk -g dtk -m 0750 /var/lib/dtk/backups
ENV DTK_BACKUP_DIR=/var/lib/dtk/backups

USER dtk

EXPOSE 8000

# No HEALTHCHECK here on purpose: the worker has no HTTP port, so a probe baked
# into the shared image would mark half the containers unhealthy. Compose gives
# each role its own check.
ENTRYPOINT ["dtk-entrypoint"]
CMD ["api"]

LABEL org.opencontainers.image.title="dtk" \
      org.opencontainers.image.description="Self-hosted Douyin and TikTok data API" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Evil0ctal/Douyin_TikTok_Download_API"
