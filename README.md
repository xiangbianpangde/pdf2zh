# pdf2zh

把英文 PDF 论文转成保留格式的中文 Markdown：**MinerU**（PDF 解析）+ **DeepSeek**（翻译）。本地全程运行，PDF 不上传第三方。

## 特性

- 📄 PDF → Markdown 保留 LaTeX 公式、表格、图片引用、章节层级
- 🌐 DeepSeek 翻译保留 Markdown 语法、文献引用编号、专有名词
- 🚀 批量处理：一次拖多个 PDF
- ⚡ 并发翻译：默认 8 并发（可调 1-32）
- 🔥 模型常驻：MinerU 模型加载一次，整个会话复用
- 🖥️ Gradio Web UI + 命令行两套界面
- 💾 断点续跑：检测到已存在的 `_zh.md` 自动跳过

---

## 安装

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

### DeepSeek API Key

在 [platform.deepseek.com](https://platform.deepseek.com) 申请 key，然后：

```powershell
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."

# Linux / macOS
export DEEPSEEK_API_KEY=sk-...
```

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
# 全流程：PDF → 英文 MD → 中文 MD
python pdf_to_zh_md.py paper.pdf -o output

# 只转换格式
python pdf_to_md.py paper.pdf -o output

# 只翻译已有英文 MD
python translate_md.py paper.md --workers 8
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

**注意**：DeepSeek 有限流，过高的并发可能触发 429。默认 8 较稳，可按账号配额上调。

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
- [DeepSeek](https://platform.deepseek.com) — 翻译
- [Gradio](https://gradio.app) — Web UI
- [OpenAI Python SDK](https://github.com/openai/openai-python) — DeepSeek 兼容客户端

---

## 许可证

MIT
