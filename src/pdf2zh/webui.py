"""Gradio Web UI —— 英文 PDF → 中文 Markdown 交付目录。

与 `pdf2zh-pipeline` 共用同一个 pipeline：
    OCR     MinerU 云 API（v4 Precision，默认；也可切 flash / 本机 mineru）
    翻译    本地配置好的 MiniMax-M3（凭据与端点由 core.config 解析）

默认输出（每个 PDF 一个子目录）：`_zh.md`、`.pdf`、`_en.md` 和 `images/`；
`--layout audit` 等价的 UI 选项追加 `manifest.json`、`validation.json`、`_mineru/`。

启动：
    pdf2zh-webui            # 安装后
    python -m pdf2zh.webui  # 未安装时
    scripts/start_mac.sh    # macOS 一键
浏览器自动打开 http://127.0.0.1:7862 （端口用 PDF2ZH_PORT 覆盖）
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator

import gradio as gr

from pdf2zh import PROJECT_ROOT
from pdf2zh.core import config, pipeline
from pdf2zh.core.contract import create_delivery_zip

OUTPUT_ROOT = Path(
    os.getenv("PDF2ZH_OUTPUT_ROOT", str(PROJECT_ROOT / "output"))
).expanduser()

OCR_MODES = {
    "云 API Precision（推荐，需 Token）": "precision",
    "云 API Flash（免 Token，≤10MB/≤20页）": "flash",
    "本机 mineru CLI（需本地模型）": "local",
}
PROVIDERS = {
    "MiniMax 官方（本地已配置）": "minimax",
    "DeepSeek 官方": "deepseek",
    "OpenRouter": "openrouter",
    "Ollama 本地": "ollama",
    "LM Studio 本地": "lmstudio",
    "自定义（下方手填）": None,
}


def _default_translate() -> config.TranslateConfig | None:
    """启动时探一次本地配置，UI 默认值跟着走（探不到就退回 minimax 预设）。"""
    try:
        return config.resolve_translate()
    except config.ConfigError:
        return None


def _normalize(files) -> list[str]:
    if not files:
        return []
    if isinstance(files, (list, tuple)):
        return [f for f in files if f]
    return [files]


def _stream(step: str, prefix: list[str], *args, **kwargs
            ) -> Iterator[tuple[str, "pipeline.ProcessResult | None"]]:
    """后台线程跑 pipeline，边跑边把日志推给 Gradio（保持界面不卡死）。

    yield (累计日志, 结果)；结果只在最后一次 yield 非 None。
    """
    q: "queue.Queue[tuple]" = queue.Queue()

    def emit(line: str) -> None:
        q.put(("log", line))

    def on_progress(done: int, total: int, idx: int) -> None:
        q.put(("progress", (done, total, idx)))

    def worker() -> None:
        try:
            runner = (pipeline.process_markdown if step == "md"
                      else pipeline.process)
            q.put(("result", runner(*args, on_progress=on_progress, emit=emit, **kwargs)))
        except Exception as exc:                      # 交给 UI 展示，不炸线程
            q.put(("error", exc))

    lines: list[str] = list(prefix)
    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, val = q.get()
        if kind == "log":
            lines.append(str(val))
            yield "\n".join(lines[-500:]), None
        elif kind == "progress":
            done, total, idx = val
            lines.append(f"      [{done}/{total}] 段落 #{idx + 1} 完成")
            yield "\n".join(lines[-500:]), None
        elif kind == "result":
            yield "\n".join(lines[-500:]), val
            return
        elif kind == "error":
            lines.append(f"[ERROR] {val}")
            yield "\n".join(lines[-500:]), None
            return


def run_workflow(pdf_files, md_files, do_convert, do_translate, ocr_mode_label,
                 ocr_model, backend, lang, start_page, end_page,
                 provider_label, base_url, model, chunk_size, workers,
                 api_key, force, layout="minimal", render_html=False):
    """批量入口：逐个 PDF 跑「云 OCR → 归位 → 翻译」，日志实时刷新。"""
    log_lines: list[str] = []
    en_outputs: list[str] = []
    zh_outputs: list[str] = []
    html_outputs: list[str] = []
    zip_outputs: list[str] = []

    def push(msg: str) -> None:
        log_lines.append(msg)

    def snapshot():
        return ("\n".join(log_lines[-500:]),
                en_outputs.copy() if en_outputs else None,
                zh_outputs.copy() if zh_outputs else None,
                html_outputs.copy() if html_outputs else None,
                zip_outputs.copy() if zip_outputs else None)

    raw_pdfs, raw_mds = _normalize(pdf_files), _normalize(md_files)
    ocr_mode = OCR_MODES.get(ocr_mode_label, "precision")
    provider = PROVIDERS.get(provider_label, config.DEFAULT_PROVIDER)

    # 只传非空值，让 config.py 走「.secrets > 环境变量 > .env > 预设」的默认链
    kwargs_common = dict(
        provider=provider,
        base_url=(base_url or None),
        model=(model or None),
        api_key=(api_key or None),
        chunk_size=int(chunk_size),
        workers=int(workers),
        force=bool(force),
        layout=layout,
        render_html=bool(render_html),
    )

    if do_convert and not raw_pdfs and raw_mds:
        push("提示：未上传 PDF，但上传了 Markdown —— 自动跳过 OCR，直接翻译。")
        do_convert = False

    inputs = raw_pdfs if do_convert else raw_mds
    if not inputs:
        push("[ERROR] " + ("请在 PDF 区上传至少一个 .pdf 文件。"
                           if do_convert else
                           "请在 Markdown 区上传至少一个 .md 文件（且不勾选 OCR）。"))
        yield snapshot()
        return

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    push(f"输出根目录：{OUTPUT_ROOT}")
    push(f"批量 {len(inputs)} 个文件｜OCR={ocr_mode}｜"
         f"翻译={'开' if do_translate else '关'}")
    yield snapshot()

    for idx, path_str in enumerate(inputs, 1):
        src = Path(path_str)
        push("")
        push(f"=== [{idx}/{len(inputs)}] {src.name} ===")
        yield snapshot()

        if do_convert:
            kwargs = dict(kwargs_common, ocr_mode=ocr_mode, ocr_model=ocr_model,
                          backend=backend, skip_translate=not do_translate,
                          lang=lang, start_page=start_page, end_page=end_page)
            step = "pdf"
        else:
            kwargs = dict(kwargs_common, skip_translate=not do_translate)
            step = "md"

        res = None
        for log_text, result in _stream(step, log_lines, src, OUTPUT_ROOT, **kwargs):
            if log_text:
                log_lines[:] = log_text.split("\n")
            if result is not None:
                res = result
            yield snapshot()

        if res is None:
            push(f"      ✗ {src.name} 处理失败，跳过")
            yield snapshot()
            continue

        push(f"      — 目录 {res.out_dir.name}/｜图片 {res.n_images} 张｜"
             f"{'中文 MD 已生成' if res.translated else '仅 OCR，未翻译'}")
        if res.en_md.is_file():
            en_outputs.append(str(res.en_md))
        if res.translated and res.zh_md.is_file():
            zh_outputs.append(str(res.zh_md))
        html_outputs.extend(str(path) for path in getattr(res, "html_paths", []) if path.is_file())
        if res.translated:
            try:
                zip_path = OUTPUT_ROOT / f"{res.out_dir.name}.zip"
                zip_outputs.append(str(create_delivery_zip(res.out_dir, zip_path,
                                       include_chinese=True)))
            except Exception as exc:
                push(f"      ⚠ ZIP 打包失败：{exc}")
        elif do_translate:
            push("      ✗ 翻译未完成，不生成完整交付 ZIP；英文与检查点保留")
        else:
            push("      — 仅 OCR，未生成完整交付 ZIP")
        yield snapshot()

    push("")
    push(f"全部完成：{len(en_outputs)} 份英文 MD，{len(zh_outputs)} 份中文 MD，{len(html_outputs)} 份 HTML")
    push(f"产物目录：{OUTPUT_ROOT}")
    yield snapshot()


def ui_open_output_dir():
    """在系统文件管理器里打开输出根目录。"""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(OUTPUT_ROOT))                      # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(OUTPUT_ROOT)])
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT_ROOT)])
        return f"已打开：{OUTPUT_ROOT}"
    except Exception as exc:
        return f"打开失败：{exc}\n手动复制路径：{OUTPUT_ROOT}"


def _apply_provider(p: str):
    """切换后端预设 → 自动填 Base URL 与模型名。"""
    preset = config.PRESETS.get(p or config.DEFAULT_PROVIDER, {})
    return gr.update(value=preset.get("base_url")), gr.update(value=preset.get("model"))


def build_ui() -> gr.Blocks:
    default_cfg = _default_translate()
    d_url = default_cfg.base_url if default_cfg else config.PRESETS["minimax"]["base_url"]
    d_model = default_cfg.model if default_cfg else config.PRESETS["minimax"]["model"]
    ocr_ready = bool(config.load_mineru_token())
    zh_hint = ("已检测到本地 MiniMax 配置，可直接留空"
               if default_cfg else "未检测到 key，可在此粘贴")

    with gr.Blocks(title="PDF → 中文 Markdown") as ui:
        gr.Markdown(
            "# 📄 英文 PDF → 中文 Markdown\n"
            "**MinerU 云 OCR + 本地 MiniMax-M3 翻译**（批量）。"
            "每个 PDF 产出一个子目录：`中文 md ＋ 原始 pdf ＋ images/`。"
        )

        with gr.Row():
            with gr.Column(scale=1, min_width=390):
                gr.Markdown("### 1) 输入")
                pdf_input = gr.File(label="英文 PDF（可多选）",
                                    file_types=[".pdf"], type="filepath",
                                    file_count="multiple")
                md_input = gr.File(label="（可选）已有英文 Markdown — 仅翻译时用",
                                   file_types=[".md"], type="filepath",
                                   file_count="multiple")

                gr.Markdown("### 2) 步骤")
                do_convert = gr.Checkbox(label="PDF → Markdown（MinerU 云 OCR）", value=True)
                do_translate = gr.Checkbox(label="Markdown → 中文", value=True)

                with gr.Accordion("OCR 设置", open=False):
                    ocr_mode_label = gr.Dropdown(
                        choices=list(OCR_MODES), value=list(OCR_MODES)[0],
                        label="OCR 方式",
                    )
                    gr.Markdown(
                        f"🔑 MinerU Token：{'已配置' if ocr_ready else '未配置（precision 不可用）'}"
                        "　来源 `.secrets/mineru.json` 或环境变量 `MINERU_TOKEN`"
                    )
                    ocr_model = gr.Radio(choices=["vlm", "pipeline"], value="vlm",
                                         label="云端解析模型（vlm 质量优先）")
                    with gr.Accordion("本机 mineru 专用", open=False):
                        backend = gr.Dropdown(
                            choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                            value="pipeline", label="本机后端")
                        lang = gr.Dropdown(choices=["en", "ch", "ch_server", "japan"],
                                           value="en", label="本机 OCR 语言提示")
                        with gr.Row():
                            start_page = gr.Number(label="起始页（0 起）", value=None, precision=0)
                            end_page = gr.Number(label="结束页（含）", value=None, precision=0)

                with gr.Accordion("翻译设置", open=False):
                    provider_label = gr.Dropdown(
                        choices=list(PROVIDERS), value="MiniMax 官方（本地已配置）",
                        label="翻译后端")
                    base_url = gr.Textbox(label="Base URL", value=d_url)
                    model = gr.Textbox(label="模型名", value=d_model)
                    api_key = gr.Textbox(label="API Key（留空=用本地已配置）",
                                         type="password", value="", placeholder=zh_hint)
                    chunk_size = gr.Slider(minimum=1000, maximum=6000, value=3000,
                                           step=100, label="单块最大字符数")
                    workers = gr.Slider(minimum=1, maximum=32, value=8, step=1,
                                        label="翻译并发数（远端 API 8 左右）")
                    provider_label.change(fn=_apply_provider, inputs=[provider_label],
                                          outputs=[base_url, model])

                with gr.Row():
                    layout = gr.Dropdown(choices=["minimal", "audit"], value="minimal",
                                         label="输出布局")
                    render_html = gr.Checkbox(label="额外生成 HTML 阅读预览", value=False)
                force = gr.Checkbox(label="强制重做（忽略已存在的中文 MD / OCR 缓存）",
                                    value=False)
                run_btn = gr.Button("▶ 开始批量处理", variant="primary", size="lg")

            with gr.Column(scale=2, min_width=520):
                gr.Markdown("### 进度")
                log_box = gr.Textbox(label="日志", lines=24, max_lines=60,
                                     autoscroll=True, interactive=False)
                gr.Markdown("### 输出")
                with gr.Row():
                    out_dir_box = gr.Textbox(
                        label=f"输出根目录（每个 PDF 一个子目录）", value=str(OUTPUT_ROOT),
                        interactive=False, scale=4)
                    open_btn = gr.Button("📂 打开文件夹", scale=1)
                open_btn.click(fn=ui_open_output_dir, outputs=[out_dir_box])
                en_files = gr.File(label="英文 Markdown（已完成）",
                                   interactive=False, file_count="multiple")
                zh_files = gr.File(label="中文 Markdown（已完成）",
                                   interactive=False, file_count="multiple")
                html_files = gr.File(label="HTML 预览（可选）",
                                     interactive=False, file_count="multiple")
                zip_files = gr.File(label="完整交付 ZIP（含图片）",
                                    interactive=False, file_count="multiple")

        run_btn.click(
            fn=run_workflow,
            inputs=[pdf_input, md_input, do_convert, do_translate, ocr_mode_label,
                    ocr_model, backend, lang, start_page, end_page,
                    provider_label, base_url, model, chunk_size, workers,
                    api_key, force, layout, render_html],
            outputs=[log_box, en_files, zh_files, html_files, zip_files],
        )

        gr.Markdown(
            "产物结构：`输出根目录/<pdf 名>/` 内含 `<pdf 名>_zh.md`、`<pdf 名>.pdf`、"
            "`images/`、`<pdf 名>_en.md`、`_mineru/`。中文 MD 已存在时自动跳过，"
            "OCR 结果缓存在 `.cache/`，重跑不重复消耗 MinerU 配额。"
        )
    return ui


def main() -> None:
    """Console-script entry point (`pdf2zh-webui`)."""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("PDF2ZH_PORT", os.getenv("GRADIO_SERVER_PORT", "7862")))
    build_ui().queue().launch(
        inbrowser=True,
        server_name="127.0.0.1",
        server_port=port,
        theme=gr.themes.Soft(),
        allowed_paths=[str(OUTPUT_ROOT)],
    )


if __name__ == "__main__":
    main()
