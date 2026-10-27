# legacy/ — 遗留脚本

这两个脚本不是 `pdf2zh` 包的一部分，也不被主流程引用。保留它们只为两个具体场景：

| 脚本 | 什么时候用 | 替代方案 |
|---|---|---|
| `pdf_to_md.py` | 需要对**本机** `mineru` CLI 传页码范围（`-s/-e`）或指定 backend，只要英文 Markdown | `pdf2zh-pipeline paper.pdf --ocr local`（不支持页码范围） |
| `batch_pdf_to_md.py` | 把 U 盘目录里的整套说明文档批量转英文 Markdown，保持子目录结构 | `pdf2zh-pipeline *.pdf --parallelism 4`（不镜像目录结构） |

运行前提：

- `pdf_to_md.py` 需要 `mineru` 在 PATH 上（`pip install -e ".[local]"`）。
- `batch_pdf_to_md.py` 同样需要本机 `mineru`，且默认源目录是 U 盘路径，
  用 `PDF2ZH_USB_ROOT` / `PDF2ZH_USB_OUT` 覆盖后再跑：

```bash
source .venv/bin/activate
PDF2ZH_USB_ROOT=/Volumes/你的U盘/说明文档目录 python legacy/batch_pdf_to_md.py
```

两个脚本都只产出英文 Markdown，不做翻译。翻译走 `pdf2zh-translate` 或
`pdf2zh-pipeline`。
