# pdf2zh

> **注意重名**：本仓库是自己的“PDF → 中文 Markdown 交付管线”实现，**不是** PyPI / GitHub 上同名的 PDFMathTranslate 项目。`pip install pdf2zh` 装到的是那个项目，与本仓库无关；本仓库没有可安装的包，只有脚本入口（见下方“快速开始”）。

英文 PDF → 中文 Markdown 的可验证交付管线：**MinerU 云 API 解析** + **OpenAI 兼容翻译模型**（默认本地配置的 MiniMax-M3）。

输入一份 PDF，默认得到一个自包含目录：中文 MD、英文 MD、原 PDF 和 `images/`；可选生成 HTML 阅读预览、审计 manifest/validation，以及包含图片的 ZIP。

## 当前特性

- MinerU 云端 OCR：保留公式、表格、图片引用和章节结构；Mac 默认不跑本地 OCR
- 图片资源按真实编码解码、校验和扩展名归一化；英文/中文图片引用顺序不一致会阻止交付
- 缓存按 PDF SHA-256 + OCR 参数指纹隔离，缓存与交付目录分离；`--force` 会跳过旧 OCR 缓存
- 默认最简目录；`--layout audit` 才生成 `manifest.json`、`validation.json` 和 `_mineru/`
- `--html` 生成流式阅读预览（不是 PDF 原生排版复刻）；公式保留 LaTeX，HTML 表格保留
- UI 自动生成完整 ZIP；CLI 生成自包含目录，下载/转交时应整体打包该目录
- 翻译分块支持断点续跑；翻译失败保留英文和 checkpoint，不生成完整中文 ZIP
- MiniMax / DeepSeek / OpenRouter / Ollama / LM Studio 等翻译后端可切换

## 快速开始

两种安装，按是否需要本地 OCR / Web UI 选一种：

```bash
python3 -m venv .venv
source .venv/bin/activate

# A. 只要云端 OCR + 翻译（约 10 MB 依赖）
pip install -r requirements-api.txt

# B. 需要 --ocr local 或 Web UI（含 mineru[all]，会拉 torch，约 1.8 GB）
# pip install -r requirements.txt

python pdf_to_zh_md.py --doctor
python pdf_to_zh_md.py paper.pdf -o ./output
```

`requirements-api.txt` 覆盖 CLI 云端路径（`--ocr precision|flash`、`--html`、资源校验、交付 ZIP）；`requirements.txt` 额外提供本地 `mineru` CLI 与 Gradio UI。两者都只装了各自需要的东西，不要叠加安装。

凭据：

| 用途 | 位置 | 来源 |
|---|---|---|
| MinerU 云 OCR | `.secrets/mineru.json` 的 `token` 或 `MINERU_TOKEN` | https://mineru.net/apiManage/token |
| 默认翻译 | `.secrets/minimax.json` 的 `key` | https://platform.minimaxi.com |

## 默认输出结构

```text
output/
└── <pdf 名>/
    ├── <pdf 名>.pdf
    ├── <pdf 名>_en.md
    ├── <pdf 名>_zh.md
    └── images/
```

缓存位于 `output/.cache/<内容与参数指纹>/`，翻译 checkpoint 位于 `output/.checkpoints/`，两者不会进入默认交付目录。

### 审计布局

```bash
python pdf_to_zh_md.py paper.pdf -o output --layout audit
```

审计布局额外生成：

```text
manifest.json
validation.json
_mineru/layout.json   # 若 OCR 返回
```

### HTML 与完整 ZIP

```bash
python pdf_to_zh_md.py paper.pdf -o output --html
```

HTML 是可搜索、可流式阅读的预览，不承诺复刻 PDF 的绝对定位和双栏版式。UI 生成的完整 ZIP 会包含 PDF、英文/中文 Markdown、HTML（如启用）、`images/` 和审计文件（如启用），不会包含缓存、checkpoint、`.secrets` 或临时文件；CLI 输出目录本身同样不包含这些运行态文件。

## 常用命令

```bash
# 免 Token 的小文件云解析
python pdf_to_zh_md.py paper.pdf --ocr flash

# 只 OCR，不翻译
python pdf_to_zh_md.py paper.pdf --skip-translate

# 强制重新 OCR 与翻译
python pdf_to_zh_md.py paper.pdf --force --refresh

# 只翻译已有英文 Markdown
python translate_md.py paper.md --provider minimax --workers 8

# Web UI
./start_mac.sh
```

## OCR 选型结论（2026-10-18）

当前不直接替换 MinerU。原因不是“模型排行榜第一”这么简单，而是本项目还要求图片资源、图片引用和最终交付目录可验证。

### 官方 OmniDocBench v1.6_full 综合榜

OpenDataLab 官方 README 的同版本表中，Overall 越高越好：

| 排名 | 模型 | Overall |
|---:|---|---:|
| 1 | TeleOCR | 96.91 |
| 2 | OvisOCR2 | 96.47 |
| 3 | PaddleOCR-VL-1.6 | 96.34 |
| 4 | MinerU2.5-Pro | 95.75 |
| 5 | GLM-OCR | 95.22 |

该 Overall 是文本、表格、公式综合指标，不是图片视觉保真率，也不包含速度、费用和图片资源交付能力。来源：

- https://raw.githubusercontent.com/opendatalab/OmniDocBench/main/README.md
- https://github.com/opendatalab/OmniDocBench

WeVisDoc 作者 README 自报的 v1.6 分数为 WeVisDoc-4B 95.38、2B 95.06；这是作者列出的端到端解析器对比，不应插入上面官方全榜。更重要的是其官方 prompt 明确要求 **Ignore figures content**，所以 WeVisDoc 单独不能解决当前图片资源问题。来源：

- https://github.com/Tencent/WeVisDoc
- https://raw.githubusercontent.com/Tencent/WeVisDoc/main/wevisdoc/prompts.py

### 推荐评测顺序

1. MinerU 云 API：保留为当前基线，先用资源契约修复后的同一输入回归。
2. PaddleOCR-VL-1.6 完整文档解析管线：优先候选，需验证图片资源、表格/公式和 PDF 页面级输出。
3. MinerU2.5-Pro 远程部署：作为同生态模型对照，固定权重与版本。
4. WeVisDoc-2B/4B：仅作为文字、公式和复杂版式对照；不能只看 Markdown 分数。
5. DeepSeek-OCR-2：备选，重点观察 grounding/裁图能力和整体分数差距。

远程主机 `ai9410-xbpd` 已确认 RTX 4090 24GB，但系统 Python 3.8、驱动 535.104.05、根盘仅约 24GB；模型、容器和缓存应放 `/home/ai9410/data`，并使用独立 Python 3.10+ / Docker 环境。本轮没有下载权重、部署模型或声称本机实测通过。

## 后续 10 个优化方向

1. **OCR 后端适配器**：统一 `parse(pdf) -> Markdown + assets + metadata` 契约，MinerU/Paddle/WeVisDoc 可插拔。
2. **页面级中间格式**：保存 page、block、bbox、order、asset relation，避免只靠最终 Markdown 反推图片顺序。
3. **图表/公式 fallback**：矢量对象或公式识别失败时生成页面/区域 PNG，并在 manifest 标注 fallback。
4. **翻译占位保护**：对公式、图片、表格、代码、URL 做 token 化保护，翻译后恢复并校验数量/顺序。
5. **OCR A/B benchmark runner**：固定 PDF 集、同一输出契约，记录准确率、图片完整率、耗时、显存、成本。
6. **并发与限流控制**：按 OCR/API/翻译分别设置 semaphore、重试、429 backoff 和成本上限。
7. **失败恢复状态机**：把 OCR、归位、翻译、校验、打包状态写成可恢复 job receipt，而不是只看文件是否存在。
8. **术语表和翻译记忆**：支持论文/项目术语表，减少同一术语在分块翻译中漂移。
9. **可视化验收页**：生成按页展示原 PDF、英文 MD、中文 MD、图片和警告的本地审计 HTML。
10. **DOCX 第二阶段**：在 Markdown/HTML 资源契约稳定后，用页面块和图片关系生成可编辑 DOCX，并记录公式降级情况。

## 安全与隐私

- MinerU 云 OCR 会上传 PDF；翻译会上传文本到所选模型服务商。
- 不要用 `--share` 或绑定 `0.0.0.0` 暴露 UI。
- 密钥只从 `.secrets`、环境变量或 `.env` 读取，不写入日志、manifest、ZIP 或 Markdown。
- 外部图片引用不被当作便携交付；无法落入 `images/` 并通过真实解码校验时，交付校验失败。

## 验证

```bash
.venv/bin/python -m pytest                       # 14 项契约测试（CI 同款）
.venv/bin/python -m pytest tests/test_resource_contract.py -v   # 单个文件
.venv/bin/python -m py_compile pipeline.py resource_contract.py html_output.py mineru_api.py pdf_to_zh_md.py app.py
```

GitHub Actions 在每次 push 到 `main` 和 PR 时跑同一套检查（`.github/workflows/ci.yml`），只装 `requirements-api.txt` + `pytest`。

## 许可证

MIT
