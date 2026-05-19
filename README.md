# pdf2zh

把英文 PDF 论文转成保留格式的中文 Markdown：**MinerU**（PDF 解析）+ 任意 **OpenAI 兼容**翻译模型（DeepSeek / Ollama / LM Studio / 自定义）。本地全程运行，PDF 不上传第三方。

## 特性

- 📄 PDF → Markdown 保留 LaTeX 公式、表格、图片引用、章节层级
- 🌐 翻译保留 Markdown 语法、文献引用编号、专有名词
- 🔌 翻译后端可切换：DeepSeek 官方 / Ollama / LM Studio / 任意 OpenAI 兼容端点
- 🚀 批量处理：一次拖多个 PDF
- ⚡ 并发翻译：默认 8 并发（可调 1-32）
- 🔥 模型常驻：MinerU 模型加载一次，整个会话复用
- 🖥️ Gradio Web UI + 命令行两套界面
- 💾 断点续跑：检测到已存在的 `_zh.md` 自动跳过

---

## 快速开始（Windows，推荐）

零命令行，**双击就能用**：

1. 装 [Python 3.10/3.11/3.12](https://www.python.org/downloads/)，安装时勾选「Add Python to PATH」
2. 下载本仓库（克隆或下 zip 解压均可）
3. 双击 `pdf2zh.bat`

首次启动会自动建 `.venv\`、装依赖（5-10 分钟，~7 GB）、可选装 CUDA PyTorch、可选保存 DeepSeek API key 到 `.env`。之后双击秒启。

详细用户手册：[USAGE.md](USAGE.md)。

---

## 手动安装（开发者 / 非 Windows）

Python 3.10–3.12（Windows 受 `ray` 限制不支持 3.13）。

```bash
git clone https://github.com/xiangbianpangde/pdf2zh.git
cd pdf2zh

python -m venv .venv
.venv\Scripts\activate    # Windows PowerShell
# source .venv/bin/activate    # Linux / macOS

pip install -r requirements.txt
```

### GPU 加速（强烈推荐）

PyPI 默认的 `torch` 是 CPU 版。GPU 用户额外执行：

```bash
# CUDA 12.8（支持 RTX 50 系 Blackwell sm_120）
pip install --force-reinstall torch torchvision \
    --index-url https://download.pytorch.org/whl/cu128

# 或 CUDA 12.6（RTX 40 / 30 系）
pip install --force-reinstall torch torchvision \
    --index-url https://download.pytorch.org/whl/cu126
```

验证 GPU 已生效：

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### 翻译后端

支持任意 **OpenAI 兼容**接口。Web UI 里有"翻译后端预设"下拉框，命令行用 `--base-url` 和 `--model`。

#### 选项 A：DeepSeek 官方（默认）

在 [platform.deepseek.com](https://platform.deepseek.com) 申请 key：

```powershell
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."

# Linux / macOS
export DEEPSEEK_API_KEY=sk-...
```

| 项 | 值 |
|---|---|
| Base URL | `https://api.deepseek.com` |
| 模型 | `deepseek-chat` / `deepseek-reasoner` |

#### 选项 B：Ollama 本地

[Ollama](https://ollama.com) 装好后拉一个适合翻译的中等模型：

```bash
ollama pull qwen2.5:14b        # 推荐，14B 中文好
# 或更小：
ollama pull qwen2.5:7b
# 或更大（显存够的话）：
ollama pull qwen2.5:32b
```

启动服务后保持后台：`ollama serve`（一般装好后自动起）。

| 项 | 值 |
|---|---|
| Base URL | `http://localhost:11434/v1` |
| 模型 | `qwen2.5:14b`（或你拉过的任何模型）|
| API Key | 任意非空字符串（如 `ollama`）|

#### 选项 C：LM Studio 本地

[LM Studio](https://lmstudio.ai) 加载好模型后，进入 **Developer** 页签 → 启动 **Local Server**。

| 项 | 值 |
|---|---|
| Base URL | `http://localhost:1234/v1` |
| 模型 | 留空或 LM Studio 里显示的模型 ID |
| API Key | 任意非空字符串 |

#### 选项 D：自定义

任何 OpenAI 兼容的端点都可以填进 `--base-url`（例如 OpenAI 官方、SiliconFlow、火山方舟、智谱、本地 vLLM 等）。

---

## 使用

### A. Web UI（推荐）

```bash
python app.py
```

浏览器自动打开 `http://127.0.0.1:7860`：

- 把 PDF 拖进"PDF 文件"框（支持多选）
- 默认勾选项已合理（pipeline 后端 / 复用 mineru-api / 并发 8）
- 点 ▶ 开始批量处理
- 每完成一个 PDF，英文 / 中文 Markdown 立即出现在下方下载列表

设置面板：
- **MinerU 设置**：后端选择、OCR 语言、起止页
- **DeepSeek 设置**：模型、分块大小、**并发数**、API key
- **mineru-api 模型服务**：手动预热、状态查看、停止

### B. 命令行

```bash
# 全流程：PDF → 英文 MD → 中文 MD（默认 DeepSeek）
python pdf_to_zh_md.py paper.pdf -o output

# 只转换格式
python pdf_to_md.py paper.pdf -o output

# 只翻译已有英文 MD（DeepSeek，默认 8 并发）
python translate_md.py paper.md --workers 8

# 用 Ollama 本地翻译
python translate_md.py paper.md \
    --base-url http://localhost:11434/v1 \
    --model qwen2.5:14b \
    --api-key ollama \
    --workers 4

# 用 LM Studio 本地翻译
python translate_md.py paper.md \
    --base-url http://localhost:1234/v1 \
    --model "" \
    --api-key lm-studio \
    --workers 4
```

### C. 通过 HTTP 驱动 UI（自动化批量）

`tools/test_batch_via_ui.py` 演示用 `gradio_client` 自动驱动正在运行的 Gradio：

```bash
# 先开 UI
python app.py

# 另起终端
python tools/test_batch_via_ui.py path/to/pdfs --workers 8
```

效果等同自动拖入所有 PDF 并点开始，进度打印到控制台。

---

## 性能参考

25 篇 ICL 论文（平均 ~15 页），RTX 5060 8GB + DeepSeek API：

| 模式 | 翻译并发 | 总耗时 |
|---|---|---|
| 串行翻译（v1）| 1 | ~4 小时 |
| 并发翻译（v2）| 8 | ~30–50 分钟 |

整个流程里翻译约占 85%，MinerU 解析约占 15%——**并发数主要影响总时长**。

**并发提示**：
- DeepSeek 官方有限流，并发过高可能 429，默认 8 较稳。
- Ollama / LM Studio 本地，并发受单卡显存和上下文长度限制；7B 模型 4-8 并发，14B 模型 2-4 并发比较合适。

---

## 安全提示

- **不要**用 `--share` 或绑 `0.0.0.0` 把 UI 暴露到公网（任何能访问你 7860 的人都能拿到 DeepSeek key）
- API key 通过 `DEEPSEEK_API_KEY` 环境变量读取，**不会**写入页面默认值（修过 `view_api` 泄露）
- 本地全程处理，PDF 不上传到 OpenDataLab / Anthropic / 其他第三方

---

## 输出结构

```
webui_output/
├── <pdf_stem>/
│   └── auto/
│       ├── <pdf_stem>.md          ← 英文
│       ├── <pdf_stem>_zh.md       ← 中文 ⭐
│       ├── images/                ← 抽取的图
│       └── <pdf_stem>_*.json      ← MinerU 中间产物（可删）
└── mineru-api.log                  ← 服务日志
```

环境变量 `PDF2ZH_OUTPUT_ROOT` 可改输出根目录。

---

## 依赖致谢

- [MinerU](https://github.com/opendatalab/MinerU) — PDF 解析
- [DeepSeek](https://platform.deepseek.com) — 默认翻译后端
- [Ollama](https://ollama.com) / [LM Studio](https://lmstudio.ai) — 本地翻译后端
- [Gradio](https://gradio.app) — Web UI
- [OpenAI Python SDK](https://github.com/openai/openai-python) — OpenAI 兼容客户端

---

## 许可证

MIT
