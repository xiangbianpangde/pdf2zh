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
├── webui_output/         ← 默认输出根目录（首次跑自动建）
├── app.py                ← Gradio UI（7860）
├── pdf_to_md.py          ← 仅 MinerU 本地解析
├── translate_md.py       ← 仅 MD → 中文
├── pdf_to_zh_md.py       ← PDF → 中文一站式（--ocr api|local，默认云 API）
├── mineru_api.py         ← MinerU 云 API OCR 模块（v4 Precision / Agent Flash）
├── pdf2zh.bat            ← Windows 启动器（macOS 不用）
└── tools/test_batch_via_ui.py
```

## 启动（macOS）

```bash
cd /Users/xbpd/Projects/pdf2zh

# 1) 激活 venv（每次新 shell 都跑）
source .venv/bin/activate

# 2) 起 Web UI（推荐）
python app.py
# 浏览器自动开 http://127.0.0.1:7860

# 3) 或者命令行单跑（默认走 MinerU 云 API OCR + MiniMax-M3，无需本地模型）
python pdf_to_zh_md.py paper.pdf -o output/

# 4) OCR 回退本地 mineru（需已装模型）
python pdf_to_zh_md.py paper.pdf -o output/ --ocr local

# 5) 只翻译已有 MD
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

命令行用 `--base-url` / `--model` / `--api-key`；不传 `--api-key` 时按
`.secrets/minimax.json` > 环境变量 顺序解析（环境变量可能残留过期 key，故 .secrets 优先）。

## Apple Silicon 注意事项

README 给的"GPU 加速"是 NVIDIA CUDA 指令，**对 M5 无效**：

- 保持 pip 默认装的 CPU 版 `torch` 即可（MPS 加速 MinerU 是否生效取决于 `mineru[all]` 内核版本，README 没承诺）；
- MinerU 解析纯 CPU 也能跑，慢 5-10 倍，但对单篇 15 页论文几分钟够用；
- 翻译慢的瓶颈在 LLM API，不在 MinerU。

## 已知坑

1. **首次启动慢**：`mineru[all]` 装包约 7 GB，5-10 分钟；首次跑 PDF 会自动下 MinerU 模型约 1-2 GB。
2. **首次 mineru-api 加载**：UI 启动时拉起 `mineru-api` 子进程（端口 30000），首次会加载模型约 60-90 秒，日志看 `webui_output/mineru-api.log`。
3. **断点续跑**：已完成的中文 MD（`<stem>_zh.md`）会被自动跳过，关闭/重启不丢活。
4. **DeepSeek 限流**：并发过高会 429，默认 8 较稳；本地 Ollama 14B 建议降到 2-4。
5. **`pdf2zh.bat` 不能用**：那是 Windows 批处理，macOS 直接 `source .venv/bin/activate && python app.py`。

## 验证

```bash
source .venv/bin/activate

# 1) 静态导入
python -c "import app, translate_md, pdf_to_md, pdf_to_zh_md, mineru_api; print('OK')"

# 2) CLI --help（不起服务）
python translate_md.py --help
python pdf_to_md.py --help

# 3) 云 API 凭据加载
python -c "import mineru_api, pdf_to_zh_md; print(bool(mineru_api.load_token()), bool(pdf_to_zh_md.load_api_key()))"

# 4) MinerU CLI（仅 --ocr local 需要）
mineru --help
mineru-api --help
```

## 故障排查

| 现象 | 排查 |
|---|---|
| `ModuleNotFoundError: mineru` | `.venv` 没激活，或 `pip install` 没跑完（看 `/tmp/pdf2zh-pip.log`） |
| `OSError: [Errno 48] Address already in use` | 7860 端口被占；`lsof -iTCP:7860 -sTCP:LISTEN` 找进程 `kill` |
| mineru-api 启动超时 | 看 `webui_output/mineru-api.log`；大概率是模型下载慢 |
| Ollama 报 connection refused | `ollama serve &` 起服务，再 `ollama list` 确认模型已拉 |
| DeepSeek 报 401 | `.env` 里 `DEEPSEEK_API_KEY` 没填或过期 |