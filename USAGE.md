# pdf2zh 使用说明

英文 PDF → 中文 Markdown。默认使用 MinerU 云端解析 + 已配置的 MiniMax 翻译，Mac 不需要本地 OCR 模型。

## 30 秒上手

```bash
source .venv/bin/activate
python pdf_to_zh_md.py --doctor
python pdf_to_zh_md.py 论文.pdf -o ./output
```

## 默认输出

```text
output/
└── 论文/
    ├── 论文.pdf
    ├── 论文_en.md
    ├── 论文_zh.md
    └── images/
```

这是默认的 **minimal** 布局。单独下载 MD 文件不会携带 `images/`，建议从 UI 下载“完整交付 ZIP”，或直接打包整个子目录。

### 审计布局

```bash
python pdf_to_zh_md.py 论文.pdf -o output --layout audit
```

`audit` 额外输出 `manifest.json`、`validation.json` 和可用的 `_mineru/` sidecar。缓存和 checkpoint 始终留在交付目录外。

### HTML 阅读预览

```bash
python pdf_to_zh_md.py 论文.pdf -o output --html
```

HTML 是流式阅读预览，不是 PDF 原生版式复刻。表格、图片引用和 LaTeX 会保留；默认不注入外部脚本，公式以可复制的 LaTeX 文本展示。

## CLI 参数

| 参数 | 作用 |
|---|---|
| `-o, --out-root` | 输出根目录，默认 `./output` |
| `--ocr precision` | MinerU 云 API Precision，默认 |
| `--ocr flash` | 小文件免 Token 云解析，受 MinerU 限制 |
| `--ocr local` | 本机 MinerU CLI；Mac 默认不推荐 |
| `--layout minimal` | 默认最简目录 |
| `--layout audit` | 额外输出 manifest/validation/_mineru |
| `--html` | 额外生成中英文 HTML 阅读预览 |
| `--skip-translate` | 只做 OCR |
| `--force` | 重做交付和翻译，并跳过旧 OCR 缓存 |
| `--refresh` | 重新调用 OCR；旧缓存保留 |
| `--workers` | 翻译并发，远端 API 通常 4–8 |
| `--no-keep-english` | 不保留英文原文和审计 sidecar |

## 断点续跑与失败行为

- OCR 缓存按 PDF SHA-256 和 OCR 参数指纹分隔，并要求完整 receipt + `READY` 标记才会命中。
- 缓存与交付目录分离，`--force` 不会复用旧 OCR 缓存。
- 翻译按块写 checkpoint；中断可重跑，已完成块会恢复。
- 翻译失败时保留英文 MD 和 checkpoint，不覆盖已有中文内容，不生成完整交付 ZIP。
- 只有中英文资源校验通过，才会被判断为完整交付物。

## 图片资源校验

硬校验包括：

- Markdown 本地图片都存在于同级 `images/`；不允许绝对路径、`..`、`.cache` 或软链接
- 文件实际编码可解码，扩展名与 MIME 一致
- 外部图片引用不能作为便携交付通过
- 英文/中文图片引用数量和顺序必须一致
- SVG 不允许脚本、外部资源、事件属性和危险实体

校验失败会让管线失败，而不是生成一个看似完成的中文文件。

## Web UI

```bash
./start_mac.sh
```

UI 与 CLI 共用同一管线。可选 `minimal/audit`、HTML 预览；完成后提供英文 MD、中文 MD、HTML（如启用）和完整 ZIP。

## OCR 方案判断

当前先不直接换掉 MinerU：

- OpenDataLab OmniDocBench v1.6_full 官方综合榜：TeleOCR 96.91、OvisOCR2 96.47、PaddleOCR-VL-1.6 96.34、MinerU2.5-Pro 95.75、GLM-OCR 95.22。
- 这些 Overall 只代表文本/表格/公式综合指标，不等于图片资源完整性、速度和成本。
- WeVisDoc 作者自报 4B 95.38、2B 95.06，但官方 prompt 明确忽略 figures content，因此不能单独解决本项目的图片问题。
- 下一步应在 RTX 4090 远程主机上，对 MinerU、PaddleOCR-VL 完整管线和 WeVisDoc 对照跑同一组 PDF；当前只确认主机环境，尚未下载权重或完成实测。

官方来源：

- https://raw.githubusercontent.com/opendatalab/OmniDocBench/main/README.md
- https://github.com/Tencent/WeVisDoc
- https://raw.githubusercontent.com/Tencent/WeVisDoc/main/wevisdoc/prompts.py

## 常见问题

| 现象 | 处理 |
|---|---|
| 图片在 Markdown 里打不开 | 检查完整 ZIP 或 `images/`；管线现在会在交付前硬校验 |
| 出现同名图片 | 管线会用内容哈希区分冲突，并禁止不明确的 basename 猜测 |
| 翻译中断 | 保留 checkpoint，降低 `--workers` 后重跑 |
| 同名 PDF 被换掉 | 管线比较 PDF SHA-256；请使用新输出根目录或明确 `--force` |
| 只下载 MD 后没图 | 这是下载方式问题；使用完整 ZIP |
| `MinerU` 401 | 检查 `.secrets/mineru.json` 的 token |

## 验证命令

```bash
.venv/bin/python -m unittest tools.test_resource_contract tools.test_translation_contract
.venv/bin/python -m py_compile pipeline.py resource_contract.py html_output.py mineru_api.py pdf_to_zh_md.py app.py
```
