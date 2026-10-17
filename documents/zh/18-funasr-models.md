# 准备 FunASR ONNX 模型

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** ——
> 自部署的抖音 / TikTok 数据接口服务：REST、MCP 和 Web 控制台，身份池自维护。
> [文档首页](../README.zh-CN.md) · [English](../en/18-funasr-models.md)

转写服务完全离线运行。镜像不会在启动时下载模型，因此第一次启用
`transcriber` profile 前，必须先在宿主机准备 Paraformer、FSMN-VAD 和
CT-Transformer 三套量化 ONNX 模型。

本文使用官方 ModelScope ONNX 仓库，最终得到以下目录：

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

`configuration.json`、模型说明和仓库元数据可以保留。不要把上面列出的运行时文件
移出各自目录，也不要把三个模型仓库多套一层同名目录。

## 模型来源

| 目录 | 组件 | 官方 ModelScope 仓库 |
|---|---|---|
| `asr/` | Paraformer-large，中文通用，16 kHz，INT8 ONNX | [`iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx`](https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx) |
| `vad/` | FSMN-VAD，中文通用，16 kHz，INT8 ONNX | [`iic/speech_fsmn_vad_zh-cn-16k-common-onnx`](https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-onnx) |
| `punc/` | CT-Transformer，中文通用，INT8 ONNX | [`iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx`](https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx) |

这里使用独立的 ASR、VAD 和标点模型，不要改用带 VAD/PUNC 的组合模型、在线流式模型、
PyTorch 仓库，或只包含 `model.onnx` 的非量化仓库。Sidecar 以
`quantize=True` 加载，文件名必须是 `model_quant.onnx`。

## 用 Git 下载（推荐）

需要 Git 和 Git LFS。macOS 可使用 Homebrew 安装 Git LFS；其他系统请使用发行版的
包管理器：

```bash
brew install git-lfs
git lfs install
```

选择一个宿主机绝对路径。下面以 `/Users/your-name/funasr-onnx` 为例；请将它替换为
自己的路径：

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

三个仓库合计需要大约 500 MiB 的工作区空间。Git LFS 还会在各仓库的 `.git/` 中保留
对象，因此刚克隆后的实际磁盘占用约为 1 GiB。

## 从网页手工下载

无法使用 Git 时，在上表的三个模型页面中分别打开 **模型文件**，下载仓库中的全部
文件，再分别放入 `asr/`、`vad/` 和 `punc/`。至少要确认以下内容：

- `asr/` 有 `model_quant.onnx`、`config.yaml`、`am.mvn` 和 `tokens.json`；
- `vad/` 有 `model_quant.onnx`、`config.yaml` 和 `am.mvn`；
- `punc/` 有 `model_quant.onnx`、`config.yaml` 和 `tokens.json`；
- 下载得到的是二进制 ONNX 文件，而不是 Git LFS 的几行文本指针；
- 目录结构是 `<根目录>/asr/model_quant.onnx`，而不是
  `<根目录>/asr/<仓库名>/model_quant.onnx`。

浏览器下载时不要只下载 `model_quant.onnx` 和 `config.yaml`。ASR 还需要 CMVN 与词表，
VAD 需要 CMVN，标点模型也需要词表。

## 让安装脚本替你下载

上面这些步骤，[引导式安装脚本](../../install/README.md) 都可以替你做：安装时（离线转写那一问
答“是”），或者之后在它的菜单里（**管理 → 11 开启或关闭转写**）。它会问模型目录在哪；目录里已经有那十个
运行时文件就直接用，否则就问你要不要通过 HTTPS 从 ModelScope 下载。

下载钉在下表那几个验证过的 commit 上，而不是仓库的当前状态；每个文件——小文件也算——都要对一遍脚本
里自带的 SHA-256。对不上的文件会被删掉而不是留着，转写保持关闭，其他部分照常装好。它只下载运行时
文件，所以占约 500 MiB，而不是 Git 克隆留下的约 1 GiB。下载好的文件会设成容器里的 UID 10001
可读。

## 检查文件完整性

先检查必需文件：

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

命令没有输出才表示这些文件均存在且非空。如果使用 Git 下载，再检查 LFS 对象：

```bash
git -C "$MODEL_ROOT/asr" lfs fsck
git -C "$MODEL_ROOT/vad" lfs fsck
git -C "$MODEL_ROOT/punc" lfs fsck
```

以下是 2026-10-13 验证过的一组快照。官方仓库更新后，提交号和 SHA-256 可能变化；
变化本身不代表下载损坏，但应重新执行后面的运行时加载测试。

| 目录 | 验证提交 | `model_quant.onnx` SHA-256 |
|---|---|---|
| `asr/` | `7162433de474912574f9d03122263776db5b046a` | `c3a06538f867c8e3d9bb9c9e1b44b126a50d732862f6c428b1b5896ee9be65c5` |
| `vad/` | `a158155ef9e81f405c052f40b6d1ad43b87e6215` | `5289eb2aa3c9af2d7a4284bcfa7c3ceb81d360814ed4203239b6c5d0569da8a1` |
| `punc/` | `8f239ff78c6267c4d859233e7eb3bbdb68c61824` | `e6cd8399bf7d0e75f8d9af4a107310e1968ecab1d50135e765b8f0265b27a83d` |

在 macOS 上可用 `shasum -a 256`，Linux 通常使用 `sha256sum`：

```bash
shasum -a 256 \
  "$MODEL_ROOT/asr/model_quant.onnx" \
  "$MODEL_ROOT/vad/model_quant.onnx" \
  "$MODEL_ROOT/punc/model_quant.onnx"
```

## 用项目运行时做离线加载测试

文件存在不等于 ONNX Runtime 一定能加载它们。下面的检查会构建当前项目的 sidecar，
然后在断网、只读文件系统、非 root 和只读模型挂载条件下初始化完整后端，包括 WeText
中文 ITN：

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

成功时最后一行是：

```text
SIDECAR_BACKEND_OK
```

部分虚拟化环境可能同时打印 ONNX Runtime 的 CPU vendor 警告；只要命令退出码为 0 且
出现上述成功行，就不影响这次模型加载检查。

## 接入 Compose

把宿主机绝对路径写入仓库根目录的 `.env`，再加一个两侧共用的口令：

```ini
DTK_TRANSCRIBER_URL=http://transcriber:9200
DTK_TRANSCRIBER_TOKEN=<openssl rand -hex 24 的输出>
DTK_TRANSCRIBER_MODEL_PATH=/Users/your-name/funasr-onnx
DTK_TRANSCRIBER_THREADS=2
DTK_TRANSCRIBER_JOB_HISTORY=64
DTK_TRANSCRIBER_PROGRESS_LOG_SECONDS=30
DTK_TRANSCRIBER_NORMALIZATION_FAILURE_POLICY=fallback
```

`DTK_TRANSCRIBER_MODEL_PATH` 是 Compose 插值读的，而插值不看根目录 `.env`，除非用
`COMPOSE_ENV_FILES=.env` 指明。不带它，上面那一行会被忽略，Docker 改挂一个空的
`docker/models/funasr-onnx`。检查 Compose 展开结果：

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml \
  --profile downloader --profile transcriber config
```

确认 `transcriber` 的 volume 来源是刚准备的绝对路径，并挂载到
`/models/funasr:ro`。随后才能启动转写服务——连同下载器一起，因为它转写的就是下载器存下的视频：

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml \
  --profile downloader --profile transcriber up -d --build
```

`--build` 是在本机构建 sidecar。Release 也会把它发布为
`evil0ctal/douyin_tiktok_download_api-transcriber`，tag 和应用镜像一致：在 `.env` 里把
`DTK_TRANSCRIBER_IMAGE` 设成这个名字，就可以拉取而不用构建。转写功能之前的 Release 没有这个 tag。

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `offline asr model is missing model_quant.onnx` | 路径多套了一层，或下载了非量化仓库 | 修正目录层级，并确认使用本文列出的 ONNX 仓库 |
| ONNX 文件只有一百多字节 | 下载到 Git LFS 指针，没有下载实际对象 | 安装 Git LFS，运行 `git lfs pull` 和 `git lfs fsck` |
| 报错缺少 `am.mvn` 或 `tokens.json` | 只手工下载了模型和配置 | 下载该模型仓库的全部文件 |
| `Permission denied` | 宿主机目录或文件不允许容器内 UID 10001 读取 | 给目录增加遍历权限、给模型文件增加读取权限；不要把它们改成全局可写 |
| Sidecar 启动时尝试联网 | 模型路径无效，或换成了会自动下载模型的其他封装 | 使用本地绝对路径和本文的三个底层 ONNX 模型；正式容器不应依赖下载回退 |
| 标点模型加载很慢 | CT-Transformer 量化文件仍约 270 MiB | 首次初始化需要读取大文件；以健康检查最终结果为准 |
| 健康状态是 `degraded`，容器里的模型目录是空的 | `DTK_TRANSCRIBER_MODEL_PATH` 写在根目录 `.env` 里，但运行 Compose 时没带 `COMPOSE_ENV_FILES=.env`，于是挂上了默认的 `docker/models/funasr-onnx` | 带上 `COMPOSE_ENV_FILES=.env` 运行 Compose，或者用安装脚本生成的 `dtkctl`，它总会带上 |
| 平时几秒就完的转写慢了好几倍，或者 `?wait=` 返回 `running` | 同样几个核上还有别的吃 CPU 的容器——通常是 `browser-rpc` 在铸造身份 | 预期内的，它会跑完。见 [转写和浏览器在同一台机器上](./02-installation.md#转写和浏览器在同一台机器上) |
