# Preparing the FunASR ONNX models

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin, TikTok and Bilibili data API: REST, MCP and a web
> console, with an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/18-funasr-models.md)

The transcription service runs fully offline. The image never downloads models
at startup, so the host must contain quantized Paraformer, FSMN-VAD and
CT-Transformer ONNX models before the `transcriber` profile is enabled.

This guide uses the official ModelScope ONNX repositories and produces this
layout:

```text
/absolute/path/to/funasr-onnx/
├── asr/
│   ├── model_quant.onnx
│   ├── config.yaml
│   ├── am.mvn
│   └── tokens.json
├── vad/
│   ├── model_quant.onnx
│   ├── config.yaml
│   └── am.mvn
└── punc/
    ├── model_quant.onnx
    ├── config.yaml
    └── tokens.json
```

It is fine to retain `configuration.json`, model documentation and repository
metadata. Do not move the runtime files above out of their component directory,
and do not add another repository-named directory between a component directory
and its files.

## Model sources

| Directory | Component | Official ModelScope repository |
|---|---|---|
| `asr/` | Paraformer-large, general Chinese, 16 kHz, INT8 ONNX | [`iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`](https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx) |
| `vad/` | FSMN-VAD, general Chinese, 16 kHz, INT8 ONNX | [`iic/speech_fsmn_vad_zh-cn-16k-common-onnx`](https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-onnx) |
| `punc/` | CT-Transformer, general Chinese, INT8 ONNX | [`iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`](https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx) |

These are three independent ASR, VAD and punctuation models. Do not substitute a
combined VAD/PUNC model, an online streaming model, a PyTorch repository, or an
unquantized repository that contains only `model.onnx`. The sidecar loads each
component with `quantize=True`, so the file must be named `model_quant.onnx`.

## Download with Git (recommended)

Git and Git LFS are required. On macOS, Git LFS can be installed with Homebrew;
use the distribution package manager on other systems:

```bash
brew install git-lfs
git lfs install
```

Choose an absolute host path. The example below uses
`/Users/your-name/funasr-onnx`; replace it with the path on your host:

```bash
MODEL_ROOT=/Users/your-name/funasr-onnx
mkdir -p "$MODEL_ROOT"

git clone --depth 1 \
  https://www.modelscope.cn/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx.git \
  "$MODEL_ROOT/asr"

git clone --depth 1 \
  https://www.modelscope.cn/iic/speech_fsmn_vad_zh-cn-16k-common-onnx.git \
  "$MODEL_ROOT/vad"

git clone --depth 1 \
  https://www.modelscope.cn/iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx.git \
  "$MODEL_ROOT/punc"
```

The three working trees need about 500 MiB. Git LFS also retains objects under
each repository's `.git/` directory, so a fresh clone occupies about 1 GiB in
total.

## Download from the web interface

If Git is unavailable, open **Model files** on each of the three pages in the
table, download every repository file, and place the results under `asr/`,
`vad/` and `punc/`, respectively. At minimum, confirm that:

- `asr/` contains `model_quant.onnx`, `config.yaml`, `am.mvn` and `tokens.json`;
- `vad/` contains `model_quant.onnx`, `config.yaml` and `am.mvn`;
- `punc/` contains `model_quant.onnx`, `config.yaml` and `tokens.json`;
- the ONNX downloads are binary files rather than short Git LFS pointer text;
- the layout is `<root>/asr/model_quant.onnx`, not
  `<root>/asr/<repository-name>/model_quant.onnx`.

Do not download only `model_quant.onnx` and `config.yaml`. ASR also needs CMVN
data and its vocabulary, VAD needs CMVN data, and the punctuation model needs
its vocabulary.

## Let the install script fetch them

The [guided install script](../../install/README.md) can do all of the above for
you, either when it installs (answer yes to offline transcription) or later from
its menu (**Manage → 11 Transcription on or off**). It asks for the model
directory, uses it as it is if the ten runtime files are already there, and
otherwise offers to download them over HTTPS from ModelScope. A directory that
already holds other files and no `asr`, `vad` or `punc` is refused: give it a
new or empty one, such as a `funasr-onnx` inside it.

The download is pinned to the verified commits in the table below rather than
to the repositories' current state, and every file — the small ones included —
is checked against a SHA-256 the script carries. A file that does not match is
deleted, not kept, and transcription stays off with everything else installed.
It fetches only the runtime files, so it needs about 500 MiB rather than the
1 GiB a Git clone keeps. The files are left readable by the container's UID
10001: the script changes permissions on the model directory itself and on its
`asr`, `vad` and `punc`, and never recursively on the path you typed.

## Check file integrity

Check the required files first:

```bash
MODEL_ROOT=/Users/your-name/funasr-onnx

for path in \
  asr/model_quant.onnx asr/config.yaml asr/am.mvn asr/tokens.json \
  vad/model_quant.onnx vad/config.yaml vad/am.mvn \
  punc/model_quant.onnx punc/config.yaml punc/tokens.json
do
  test -s "$MODEL_ROOT/$path" || echo "missing or empty: $path"
done
```

No output means that all of these files exist and are non-empty. For a Git
download, verify the LFS objects as well:

```bash
git -C "$MODEL_ROOT/asr" lfs fsck
git -C "$MODEL_ROOT/vad" lfs fsck
git -C "$MODEL_ROOT/punc" lfs fsck
```

The following snapshot was verified on 2026-10-13. Official repository updates
may change the commit IDs and SHA-256 values. A difference does not by itself
mean that the download is corrupt, but the runtime loading test below should be
repeated.

| Directory | Verified commit | `model_quant.onnx` SHA-256 |
|---|---|---|
| `asr/` | `7162433de474912574f9d03122263776db5b046a` | `c3a06538f867c8e3d9bb9c9e1b44b126a50d732862f6c428b1b5896ee9be65c5` |
| `vad/` | `a158155ef9e81f405c052f40b6d1ad43b87e6215` | `5289eb2aa3c9af2d7a4284bcfa7c3ceb81d360814ed4203239b6c5d0569da8a1` |
| `punc/` | `8f239ff78c6267c4d859233e7eb3bbdb68c61824` | `e6cd8399bf7d0e75f8d9af4a107310e1968ecab1d50135e765b8f0265b27a83d` |

Use `shasum -a 256` on macOS. Linux commonly provides `sha256sum` instead:

```bash
shasum -a 256 \
  "$MODEL_ROOT/asr/model_quant.onnx" \
  "$MODEL_ROOT/vad/model_quant.onnx" \
  "$MODEL_ROOT/punc/model_quant.onnx"
```

## Test an offline load with the project runtime

Files can exist even when ONNX Runtime cannot load them. This check builds the
current sidecar and initializes the complete backend, including Chinese WeText
ITN, with no network, a read-only filesystem, a non-root user and a read-only
model mount:

```bash
MODEL_ROOT=/Users/your-name/funasr-onnx

docker build \
  -f docker/Dockerfile.transcriber \
  -t dtk-transcriber:model-check \
  .

docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:size=256m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --user 10001:10001 \
  -e DTK_TRANSCRIBER_MODEL_DIR=/models/funasr \
  -e DTK_TRANSCRIBER_THREADS=2 \
  -v "$MODEL_ROOT:/models/funasr:ro" \
  dtk-transcriber:model-check \
  python -c 'from app import _validate_models, FunASRBackend; _validate_models(); FunASRBackend(); print("SIDECAR_BACKEND_OK")'
```

The final line on success is:

```text
SIDECAR_BACKEND_OK
```

Some virtualized environments also print an ONNX Runtime CPU vendor warning.
It does not invalidate this model-loading check when the command exits with zero
and prints the success line above.

## Connect the models to Compose

Add the absolute host path to `.env` at the repository root, with a token both
sides will share:

```ini
DTK_TRANSCRIBER_URL=http://transcriber:9200
DTK_TRANSCRIBER_TOKEN=<output of: openssl rand -hex 24>
DTK_TRANSCRIBER_MODEL_PATH=/Users/your-name/funasr-onnx
DTK_TRANSCRIBER_THREADS=2
DTK_TRANSCRIBER_JOB_HISTORY=64
DTK_TRANSCRIBER_PROGRESS_LOG_SECONDS=30
DTK_TRANSCRIBER_NORMALIZATION_FAILURE_POLICY=fallback
```

`DTK_TRANSCRIBER_MODEL_PATH` is read by Compose interpolation, which does not
look at the root `.env` unless `COMPOSE_ENV_FILES=.env` says so. Without it the
line above is ignored and Docker mounts an empty `docker/models/funasr-onnx`
instead. Inspect the resolved Compose configuration:

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml \
  --profile downloader --profile transcriber config
```

Confirm that the `transcriber` volume source is the absolute path just prepared
and that it is mounted at `/models/funasr:ro`. The transcription service can
then be started — with the downloader, since what it transcribes is a video the
downloader stored:

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml \
  --profile downloader --profile transcriber up -d --build
```

`--build` builds the sidecar on this machine. Releases also publish it as
`evil0ctal/douyin_tiktok_download_api-transcriber`, with the same tags as the
application image: set `DTK_TRANSCRIBER_IMAGE` to that name in `.env` and pull
instead of building. A release from before transcription has no such tag.

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `offline asr model is missing model_quant.onnx` | The path has an extra directory level, or the unquantized repository was downloaded | Correct the layout and use the ONNX repositories listed here |
| An ONNX file is only about a hundred bytes | It is a Git LFS pointer, not the object | Install Git LFS, then run `git lfs pull` and `git lfs fsck` |
| `am.mvn` or `tokens.json` is missing | Only the model and configuration were downloaded by hand | Download every file in that model repository |
| `Permission denied` | Host permissions prevent UID 10001 in the container from reading the files | Grant directory traversal and file read permission; do not make the models world-writable |
| The sidecar tries to use the network at startup | The local path is invalid, or a different wrapper with automatic downloads was substituted | Use an absolute local path and the three low-level ONNX models in this guide; production must not rely on a download fallback |
| The punctuation model loads slowly | The quantized CT-Transformer file is still about 270 MiB | Initial loading must read the file; wait for the final health result |
| Health is `degraded` and the model directory in the container is empty | `DTK_TRANSCRIBER_MODEL_PATH` sits in the root `.env`, but Compose was run without `COMPOSE_ENV_FILES=.env`, so the default `docker/models/funasr-onnx` was mounted | Run Compose with `COMPOSE_ENV_FILES=.env`, or through the install script's `dtkctl`, which always sets it |
| A transcription that normally takes seconds takes many times longer, or `?wait=` comes back `running` | Another CPU-bound container is on the same cores — usually `browser-rpc` minting identities | Expected, and it finishes. See [when transcription and the browser share a host](./02-installation.md#when-transcription-and-the-browser-share-a-host) |
