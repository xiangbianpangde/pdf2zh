# 交付契约

这份契约是**唯一**判定「完成」的依据。脚本按它写文件，`validate.py` 按它判失败。

## 1. 目录

```
<out_root>/<stem>/
├── <stem>.md              MinerU 的 Markdown，图片链接已改写为 images/...
├── <stem>_zh.md           中文翻译；未翻译或翻译失败时不存在
├── <stem>.pdf             源 PDF 副本（字节级一致，用 sha256 校验）
├── images/                归一化后的图片，扩展名与真实编码一致
└── _meta/
    ├── manifest.json      运行指纹、参数、图片清单
    ├── validation.json    校验结果（ok / issues / warnings）
    ├── completion.json    完成标记（含参数指纹）；缺失就重跑
    └── content_list.json  MinerU 结构 JSON（仅 precision 通道）
```

同级的 `<out_root>/.cache/` 与 `<out_root>/.checkpoints/` 是运行态，**永远不属于交付**，可以随时删除。

### `<stem>` 的生成规则

1. 取源文件名去扩展名；
2. `/ \ : * ? " < > |` 与控制字符（`\x00-\x1f`）替换为 `_`；
3. 连续空白折叠为一个空格；
4. 去掉首尾的空格与点号；
5. 截断到 120 个字符；结果为空则用 `document`。

两个输入映射到同一个 `<stem>` 时，CLI 直接拒绝（退出码 2），不会静默覆盖。

## 2. 图片

### 落地规则

| 规则 | 原因 |
|---|---|
| 格式由**文件头字节**判定，不看后缀 | MinerU 会输出 PNG 字节但命名为 `.jpg` |
| 落地后缀按真实编码改正（`figure.jpg` → `figure.png`） | 否则阅读器/Markdown 预览会失败 |
| 内容相同 → 复用同一文件 | 重复引用不产生副本 |
| 同名但内容不同 → 追加 `-<sha256前12位>` | 不静默覆盖 |
| SVG 拒绝 `script`/`on*`/`foreignObject`/外部 `href`/DTD | 交付物要能安全打开 |
| 只放 `images/`，不嵌套子目录 | Markdown 引用统一为 `images/<name>` |

可选校验：环境里装了 Pillow 就用它做一次真实解码（`manifest.images[].verified_by = "pillow"`），没装则退化为文件头解析（`verified_by = "header"`）。**不强制依赖 Pillow。**

### 链接改写规则

支持的引用形式（示例放在代码块里，避免被当成真实链接）：

```text
行内        ![alt](dest)
尖括号      ![alt](<dest with space.png>)
HTML        <img src="dest">
引用式      ![alt][id]   +   [id]: dest
快捷引用    ![alt]       +   [alt]: dest
```

- 代码围栏、行内代码、HTML 注释里的示例**不改写**。
- 只做**精确或规范化后精确**的匹配；basename 有歧义时宁可不改，也不猜。
- 引用式图片按**使用顺序**记录，不按定义位置，所以定义写在文件末尾也不影响中英序列比对。

## 3. 校验规则（硬失败）

`validate.py` 输出 `issues`（硬失败）与 `warnings`（提示）。任一条 issue 存在即 `ok = false`。

| code | 含义 |
|---|---|
| `missing_markdown` | Markdown 不存在或为空 |
| `unreadable_markdown` | 不是合法 UTF-8 |
| `external_image` | 引用了 http/https/data 图片：不是便携交付 |
| `non_portable_image_path` | 绝对路径、`..`、`.cache`、Windows 盘符 |
| `image_outside_assets` | 解析后的真实路径不在 `images/`，或经过软链接 |
| `missing_image` | 引用存在但文件缺失 |
| `image_reference_parity_mismatch` | 中英两版的图片引用序列不一致（数量或顺序） |
| `extension_mismatch` | 后缀与真实编码不符 |
| `undecodable_image` | 无法解码（截断、损坏、不支持的编码） |

`warnings` 目前只有 `tiny_image`（宽或高小于 16px）。

「图片序列一致」用**使用顺序**比较：引用式定义写在文件末尾时，按引用出现的位置算，不按定义位置算。

## 4. manifest.json

```json
{
  "version": 1,
  "stem": "paper",
  "channel": "precision",
  "params": {"channel": "precision", "ocr_model": "vlm", "language": "en", "pages": null},
  "source": {"name": "paper.pdf", "sha256": "…", "bytes": 12345},
  "markdown": "paper.md",
  "images": [
    {"source": "images/figure.jpg", "output": "images/figure.png",
     "mime": "image/png", "width": 1280, "height": 720, "bytes": 12345,
     "sha256": "…", "verified_by": "pillow"}
  ]
}
```

## 5. 复用与重跑

| 情况 | 行为 |
|---|---|
| `completion.json` 指纹匹配且校验通过 | 跳过，打印「已有有效交付」 |
| OCR 缓存命中（源 sha256 + 通道参数一致） | 不调 MinerU，直接复用解包结果 |
| `--force` | 忽略完成标记与 OCR 缓存，全部重做 |
| 源 PDF 内容改变但 `<stem>` 未变 | 与已有 `<stem>.pdf` 的 sha256 不同 → 拒绝，要求换目录或 `--force` |
| 翻译失败 | 英文与检查点保留，`_zh.md` 与 `completion.json` 都不写 |

缓存目录结构：`<out_root>/.cache/<sha256(源+参数)[:32]>/`，内含 `receipt.json`、`READY`、`runs/<时间>-<随机>/`。只有 `receipt` 与 `READY` 同时存在、且 `source`/`params` 完全匹配时才命中。

失败时不会留下半成品：OCR 阶段失败会清掉空的运行目录，交付目录不会出现。
