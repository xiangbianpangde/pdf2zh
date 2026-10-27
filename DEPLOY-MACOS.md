# pdf2zh on macOS（Apple Silicon）

macOS 上部署 `xiangbianpangde/pdf2zh` 的笔记。Windows 用户看 README 走 `pdf2zh.bat`。

## 系统现状（部署时确认）

- macOS 26.5.1 (Darwin 25.5.0)，arm64（Apple Silicon，M5）
- Python 3.12.7（来自系统 Anaconda `/opt/anaconda3/bin/python3.12`）
- Conda 24.9.2（未使用 conda env——pdf2zh 用 venv，避开 conda + ray/PyTorch 的兼容坑）
- Ollama 0.32.x 已装但**未启动**（`ollama serve` 时起）

## 项目布局

```
pdf2zh/
├── .venv/                ← 已建（python3.12 -m venv）
├── .env / .env.example   ← 已建（key 留空，由用户填）
├── .secrets/             ← 云 API 凭据（git 忽略；mineru.json / minimax.json）
├── output/               ← 默认输出根目录（交付子目录 + 外置 .cache/.checkpoints）
├── app.py                ← Gradio UI（7862）
├── start_mac.sh          ← macOS 一键启动
├── config.py             ← 凭据解析（.env + .secrets → 统一翻译配置）
├── pipeline.py           ← 管线核心：云 OCR → 产物归位 → 翻译（UI 与 CLI 共用）
├── mineru_api.py         ← MinerU 云 API OCR 模块（v4 Precision / Agent Flash）
├── pdf_to_zh_md.py       ← 主入口：PDF → 中文 md + pdf 副本 + images/
├── translate_md.py       ← 仅 MD → 中文（也可单独当翻译器）
├── pdf_to_md.py          ← 遗留：MinerU 本地解析（需页码范围时用）
├── pdf2zh.bat            ← Windows 启动器（macOS 不用）
└── tools/batch_pdf_to_md.py  ← 遗留：U 盘目录批量转英文 MD
```

## 启动（macOS）

```bash
cd /Users/xbpd/Projects/pdf2zh

# 1) 激活 venv（每次新 shell 都跑）
source .venv/bin/activate

# 2) 自检凭据（MinerU token + MiniMax key）
python pdf_to_zh_md.py --doctor

# 3) 起 Web UI（推荐）
./start_mac.sh
# 浏览器自动开 http://127.0.0.1:7862

# 4) 或者命令行单跑（默认走 MinerU 云 API OCR + MiniMax-M3，无需本地模型）
python pdf_to_zh_md.py paper.pdf -o output/

# 5) OCR 回退本地 mineru（需已装模型）
python pdf_to_zh_md.py paper.pdf -o output/ --ocr local

# 6) 只翻译已有 MD
python translate_md.py paper.md --workers 8
```

## OCR 选型（2026-09-30 起：默认云 API）

| 方案 | 优点 | 缺点 | 推荐度 |
|---|---|---|---|
| **MinerU 云 API（v4 Precision）** | 零本地资源、免下 3.4GB 模型、质量优先（vlm） | 需 Token；大文件上传耗时 | ⭐⭐⭐ 默认 |
| MinerU Agent Flash | 免 Token | 限 ≤10MB / ≤20 页 | ⭐ 小文档备用 |
| 本地 mineru CLI | 离线可用 | 占内存、首次下模型 | ⭐ 无网回退 |

凭据：`.secrets/mineru.json` 的 `token` 字段（https://mineru.net/apiManage/token 创建）；
回退顺序 环境变量 `MINERU_TOKEN` > 本项目 `.secrets` > 每日早报共享 `.secrets`。

## 翻译后端选型（按本机实际条件）

| 方案 | 优点 | 缺点 | 推荐度 |
|---|---|---|---|
| **MiniMax-M3 API** | 已有 key（`.secrets/minimax.json`），OpenAI 兼容端点直连 | 收费 | ⭐⭐⭐ 默认 |
| Ollama 本地 qwen2.5:14b | 离线、不限速、零成本 | 14B 吃 ~10 GB 内存；首次拉模型慢 | ⭐⭐ 离线场景 |
| DeepSeek 官方 API | 模型强 | 收费 + 限流 429 | ⭐ 备选 |

命令行用 `--provider` 切后端，用 `--base-url` / `--model` / `--api-key` 覆盖。
不传时由 `config.py` 按 `显式参数 > .secrets > 环境变量 > .env > 内置预设` 解析
（环境变量可能残留过期 key，故 `.secrets` 优先）。`--doctor` 可一次看全。

## Apple Silicon 注意事项

README 给的"GPU 加速"是 NVIDIA CUDA 指令，**对 M5 无效**：

- 保持 pip 默认装的 CPU 版 `torch` 即可（MPS 加速 MinerU 是否生效取决于 `mineru[all]` 内核版本，README 没承诺）；
- MinerU 解析纯 CPU 也能跑，慢 5-10 倍，但对单篇 15 页论文几分钟够用；
- 翻译慢的瓶颈在 LLM API，不在 MinerU。

## 已知坑

1. **首次启动慢**：`mineru[all]` 装包约 7 GB，5-10 分钟；用 `--ocr local` 首次跑会下 MinerU 模型约 1-2 GB（默认云 OCR 不需要）。
2. **UI 不再拉起 mineru-api 子进程**：2026-10-01 起默认走云 OCR，本机不再常驻模型服务，所以没有 30000 端口和模型预热日志；端口 30000 被占也不再影响使用。
3. **断点续跑**：只有 PDF 内容指纹、参数、英文/中文 MD 和资源校验均匹配时才跳过；OCR 缓存在 `output/.cache/<content-and-params-hash>/`，要求 receipt + `READY` 标记。`--force` 跳过旧 OCR 缓存，`--refresh` 保留旧缓存并创建新 run。
4. **交付下载**：默认目录只含 PDF、英文/中文 MD 和 `images/`；UI 的完整 ZIP 才是可直接转交的自包含包，HTML 通过 `--html` 或 UI 选项启用。
5. **远端限流**：并发过高会 429，`--workers 8` 较稳；本地 Ollama 14B 建议降到 2-4。
6. **`pdf2zh.bat` 不能用**：那是 Windows 批处理，macOS 用 `./start_mac.sh`。

## 验证

```bash
source .venv/bin/activate

# 1) 静态导入
python -c "import app, pipeline, config, translate_md, pdf_to_md, pdf_to_zh_md, mineru_api; print('OK')"

# 2) CLI --help（不起服务）
python pdf_to_zh_md.py --help
python translate_md.py --help

# 3) 凭据自检（列出所有后端就绪状态）
python pdf_to_zh_md.py --doctor

# 4) 端到端冒烟（约 2-3 分钟/15 页论文）
python pdf_to_zh_md.py /path/to/en.pdf -o output/

# 5) MinerU CLI（仅 --ocr local 需要）
mineru --help
```

## 故障排查

| 现象 | 排查 |
|---|---|
| `ModuleNotFoundError: mineru` | `.venv` 没激活，或 `pip install` 没跑完（看 `/tmp/pdf2zh-pip.log`） |
| `OSError: [Errno 48] Address already in use` | 7862 端口被占；`lsof -iTCP:7862 -sTCP:LISTEN` 找进程 `kill`，或换 `PDF2ZH_PORT=7863 ./start_mac.sh` |
| `缺少 minimax 的 API key` | `.secrets/minimax.json` 写 `{"key": "sk-..."}`，或设 `MINIMAX_API_KEY`；跑 `--doctor` 看提示 |
| MinerU 报 401 / 额度不足 | `.secrets/mineru.json` 的 token 过期或额度用尽，去 https://mineru.net/apiManage/token 确认 |
| 翻译中途报 429 | 调小 `--workers`（如 4），重跑会跳过已完成的 PDF |
| Ollama 报 connection refused | `ollama serve &` 起服务，再 `ollama list` 确认模型已拉 |