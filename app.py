"""Gradio web UI for the PDF -> English MD -> Chinese MD workflow.

Run:
    C:\\Users\\yhn\\mineru\\.venv\\Scripts\\Activate.ps1
    python C:\\Users\\yhn\\mineru\\scripts\\app.py
Browser opens at http://127.0.0.1:7860 automatically.

Batch mode: upload multiple PDFs (or MDs); they are processed sequentially.
By default a persistent `mineru-api` server is started once per session and
reused across all PDFs — model load (~1 minute) happens once, then each PDF
just streams over HTTP.
"""
import atexit
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import gradio as gr
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
from translate_md import chunk_markdown, translate_chunks_concurrent


OUTPUT_ROOT = Path(os.getenv("PDF2ZH_OUTPUT_ROOT",
                              str(Path(__file__).resolve().parent / "webui_output")))
PROGRESS_NOISE = re.compile(r"\d+%\||it/s\]|s/it\]|Fetching \d+ files:")

API_HOST = "127.0.0.1"
API_PORT = 30000
API_URL = f"http://{API_HOST}:{API_PORT}"
API_LOG = OUTPUT_ROOT / "mineru-api.log"
API_STARTUP_TIMEOUT = 240  # seconds

# Module-level state for the persistent mineru-api server
_api_proc: subprocess.Popen | None = None


def _is_noise(line: str) -> bool:
    return bool(PROGRESS_NOISE.search(line))


def _find_md(out_dir: Path, stem: str) -> Path | None:
    matches = list(out_dir.rglob(f"{stem}.md"))
    return matches[0] if matches else None


def _normalize_files(files) -> list[str]:
    if not files:
        return []
    if isinstance(files, (list, tuple)):
        return [f for f in files if f]
    return [files]


def _ping_api(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/docs", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


def ensure_api_server() -> Iterator[str]:
    """Start mineru-api if not already running. Yields log lines, returns when ready."""
    global _api_proc

    if _ping_api():
        yield f"      mineru-api 已运行 @ {API_URL}"
        return

    if _api_proc is not None and _api_proc.poll() is None:
        yield f"      mineru-api 进程存活但无响应，强制重启 ..."
        _api_proc.terminate()
        try:
            _api_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _api_proc.kill()
        _api_proc = None

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    yield f"      启动 mineru-api (host={API_HOST}, port={API_PORT}) 日志→ {API_LOG.name}"

    log_fh = open(API_LOG, "ab", buffering=0)
    _api_proc = subprocess.Popen(
        ["mineru-api", "--host", API_HOST, "--port", str(API_PORT)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    deadline = time.time() + API_STARTUP_TIMEOUT
    last_msg_t = 0.0
    while time.time() < deadline:
        if _api_proc.poll() is not None:
            yield f"      [ERROR] mineru-api 退出 (code={_api_proc.returncode})。查看 {API_LOG}"
            return
        if _ping_api(timeout=1.5):
            elapsed = int(API_STARTUP_TIMEOUT - (deadline - time.time()))
            yield f"      ✓ mineru-api 就绪 @ {API_URL} (耗时 ~{elapsed}s)"
            return
        if time.time() - last_msg_t > 8:
            yield f"      ... 等待 mineru-api 就绪 (首次启动需加载模型)"
            last_msg_t = time.time()
        time.sleep(2)
    yield f"      [ERROR] mineru-api 启动超时 (>{API_STARTUP_TIMEOUT}s)"


def _cleanup_api():
    global _api_proc
    if _api_proc is not None and _api_proc.poll() is None:
        _api_proc.terminate()
        try:
            _api_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _api_proc.kill()


atexit.register(_cleanup_api)


def _convert_pdf(pdf: Path, out_root: Path, backend: str, lang: str,
                 start_page, end_page, api_url: str | None
                 ) -> Iterator[tuple[str, Path | None]]:
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = ["mineru", "-p", str(pdf), "-o", str(out_root.resolve()),
           "-b", backend, "-l", lang]
    if start_page is not None and int(start_page) >= 0:
        cmd += ["-s", str(int(start_page))]
    if end_page is not None and int(end_page) >= 0:
        cmd += ["-e", str(int(end_page))]
    if api_url:
        cmd += ["--api-url", api_url]

    yield f"      命令: {' '.join(cmd)}", None

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line or _is_noise(line):
            continue
        yield f"      {line}", None
    proc.wait()
    if proc.returncode != 0:
        yield f"[ERROR] MinerU 退出码 {proc.returncode}", None
        return

    en_md = _find_md(out_root / pdf.stem, pdf.stem)
    if not en_md:
        yield f"[ERROR] 未找到生成的 markdown ({out_root / pdf.stem})", None
        return
    yield f"      ✓ 英文 MD: {en_md}", en_md


def _translate_one(en_md: Path, model: str, chunk_size: int, workers: int,
                   api_key: str, base_url: str = "https://api.deepseek.com"
                   ) -> Iterator[tuple[str, Path | None]]:
    import queue
    import threading

    md_text = en_md.read_text(encoding="utf-8")
    chunks = chunk_markdown(md_text, chunk_size)
    yield f"      {len(chunks)} 个分块, 模型={model}, 并发={workers}", None

    client = OpenAI(api_key=api_key, base_url=base_url)

    # Bridge from worker thread (translate_chunks_concurrent runs sync) to this
    # generator's yields by spawning a thread for the parallel work and feeding
    # progress via a queue.
    q: "queue.Queue[tuple]" = queue.Queue()
    result_box: list = [None, None]  # [results_list, exception]

    def on_progress(done, total, idx):
        q.put(("progress", done, total, idx))

    def worker():
        try:
            result_box[0] = translate_chunks_concurrent(
                client, chunks, model=model,
                max_workers=workers, on_progress=on_progress,
            )
        except Exception as e:
            result_box[1] = e
        finally:
            q.put(("done",))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        msg = q.get()
        if msg[0] == "progress":
            _, done, total, idx = msg
            yield f"      [{done}/{total}] chunk #{idx + 1} ok", None
        elif msg[0] == "done":
            break

    t.join()
    if result_box[1] is not None:
        raise result_box[1]

    translated = result_box[0]
    zh_md = en_md.with_name(en_md.stem + "_zh.md")
    zh_md.write_text("\n\n".join(translated), encoding="utf-8")
    yield f"      ✓ 中文 MD: {zh_md}", zh_md


def run_workflow(pdf_files, md_files, do_convert, do_translate, reuse_server,
                 backend, lang, start_page, end_page,
                 model, chunk_size, workers, api_key):
    log_lines: list[str] = []
    en_outputs: list[str] = []
    zh_outputs: list[str] = []

    def push(msg: str):
        log_lines.append(msg)

    def snapshot():
        return (
            "\n".join(log_lines[-500:]),
            en_outputs.copy() if en_outputs else None,
            zh_outputs.copy() if zh_outputs else None,
        )

    pdf_list = _normalize_files(pdf_files) if do_convert else []
    md_list = _normalize_files(md_files) if not do_convert else []

    if do_convert and not pdf_list:
        push("[ERROR] 请上传至少一个 PDF")
        yield snapshot()
        return
    if not do_convert and not md_list:
        push("[ERROR] 未勾选 PDF→MD，需要上传至少一个英文 Markdown")
        yield snapshot()
        return
    if do_translate:
        if not api_key:
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            push("[ERROR] 缺少 DeepSeek API key（设置 DEEPSEEK_API_KEY 环境变量或在 UI 中填入）")
            yield snapshot()
            return

    inputs = pdf_list if do_convert else md_list
    total = len(inputs)
    push(f"批量处理 {total} 个文件 (convert={do_convert}, translate={do_translate}, "
         f"reuse_server={reuse_server})")
    yield snapshot()

    api_url = None
    if do_convert and reuse_server:
        push("--- 检查 / 启动 mineru-api 常驻服务 ---")
        yield snapshot()
        for line in ensure_api_server():
            push(line)
            yield snapshot()
        if _ping_api():
            api_url = API_URL
        else:
            push("      ⚠ 服务未就绪，回退到单 PDF 临时实例（每个 PDF 重新加载模型）")
            yield snapshot()

    for idx, path_str in enumerate(inputs, 1):
        in_path = Path(path_str)
        push("")
        push(f"=== [{idx}/{total}] {in_path.name} ===")
        yield snapshot()

        if do_convert:
            existing_en = OUTPUT_ROOT / in_path.stem / "auto" / f"{in_path.stem}.md"
            existing_zh = existing_en.with_name(existing_en.stem + "_zh.md")
            if do_translate and existing_zh.exists():
                push(f"      跳过：中文 MD 已存在 {existing_zh.name}")
                en_outputs.append(str(existing_en))
                zh_outputs.append(str(existing_zh))
                yield snapshot()
                continue
            if not do_translate and existing_en.exists():
                push(f"      跳过：英文 MD 已存在 {existing_en.name}")
                en_outputs.append(str(existing_en))
                yield snapshot()
                continue

            push(f"[1/2] MinerU 转换 ({backend}, lang={lang}, "
                 f"{'reuse server' if api_url else 'temp instance'})")
            yield snapshot()

            en_md: Path | None = None
            for line, result in _convert_pdf(in_path, OUTPUT_ROOT,
                                             backend, lang, start_page, end_page,
                                             api_url):
                push(line)
                if result is not None:
                    en_md = result
                yield snapshot()

            if en_md is None:
                push("      跳过 (转换失败)")
                yield snapshot()
                continue
            en_outputs.append(str(en_md))
            yield snapshot()
        else:
            en_md = in_path
            push(f"[1/2] 跳过 PDF 转换，使用 {en_md.name}")
            en_outputs.append(str(en_md))
            yield snapshot()

        if not do_translate:
            continue

        push("[2/2] DeepSeek 翻译")
        yield snapshot()
        zh_md: Path | None = None
        try:
            for line, result in _translate_one(en_md, model, int(chunk_size),
                                                int(workers), api_key):
                push(line)
                if result is not None:
                    zh_md = result
                yield snapshot()
        except Exception as e:
            push(f"[ERROR] 翻译失败: {e}")
            yield snapshot()
            continue

        if zh_md is not None:
            zh_outputs.append(str(zh_md))
            yield snapshot()

    push("")
    push(f"全部完成: {len(en_outputs)} 个英文 MD, {len(zh_outputs)} 个中文 MD")
    if _ping_api():
        push(f"mineru-api 仍在 {API_URL} 待命，下次批处理可直接使用")
    yield snapshot()


def ui_status_api():
    """For the status refresh button."""
    if _ping_api():
        return f"🟢 运行中 — {API_URL}（日志：{API_LOG}）"
    if _api_proc is not None and _api_proc.poll() is None:
        return f"🟡 启动中（PID {_api_proc.pid}），稍候 ..."
    return "🔴 未启动（首次批处理时会自动启动）"


def ui_start_api():
    lines = []
    for line in ensure_api_server():
        lines.append(line.strip())
    return "\n".join(lines) + "\n\n" + ui_status_api()


def ui_stop_api():
    global _api_proc
    if _api_proc is None or _api_proc.poll() is not None:
        return "(没有正在运行的 mineru-api 进程)\n\n" + ui_status_api()
    _api_proc.terminate()
    try:
        _api_proc.wait(timeout=10)
        msg = f"已停止 (PID 退出码 {_api_proc.returncode})"
    except subprocess.TimeoutExpired:
        _api_proc.kill()
        msg = "已强制停止"
    _api_proc = None
    return msg + "\n\n" + ui_status_api()


def build_ui():
    with gr.Blocks(title="PDF → 中文 Markdown") as app:
        gr.Markdown(
            "# 📄 PDF → 中文 Markdown（支持批量 + 常驻模型服务）\n"
            "MinerU 解析 + DeepSeek 翻译。批量模式下复用 mineru-api 常驻进程，"
            "模型只加载一次。"
        )

        with gr.Row():
            with gr.Column(scale=1, min_width=380):
                gr.Markdown("### 1) 输入")
                pdf_input = gr.File(
                    label="PDF 文件（可多选）",
                    file_types=[".pdf"],
                    type="filepath",
                    file_count="multiple",
                )
                md_input = gr.File(
                    label="（可选）英文 Markdown — 仅翻译时用（可多选）",
                    file_types=[".md"],
                    type="filepath",
                    file_count="multiple",
                )

                gr.Markdown("### 2) 步骤")
                do_convert = gr.Checkbox(label="PDF → Markdown（MinerU）", value=True)
                do_translate = gr.Checkbox(label="Markdown → 中文（DeepSeek）", value=True)
                reuse_server = gr.Checkbox(
                    label="复用 mineru-api 常驻服务（批量强烈推荐）",
                    value=True,
                )

                with gr.Accordion("MinerU 设置", open=False):
                    backend = gr.Dropdown(
                        choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                        value="pipeline",
                        label="后端（pipeline = 最省显存，8GB 卡推荐）",
                    )
                    lang = gr.Dropdown(
                        choices=["en", "ch", "ch_server", "ch_lite", "japan", "korean"],
                        value="en",
                        label="OCR 语言提示",
                    )
                    with gr.Row():
                        start_page = gr.Number(label="起始页（0 起，留空=全部）",
                                               value=None, precision=0)
                        end_page = gr.Number(label="结束页（含此页）",
                                             value=None, precision=0)

                with gr.Accordion("DeepSeek 设置", open=False):
                    _has_env_key = bool(os.getenv("DEEPSEEK_API_KEY"))
                    api_key = gr.Textbox(
                        label="API Key（留空=用 DEEPSEEK_API_KEY 环境变量）",
                        type="password",
                        value="",
                        placeholder=("已检测到 DEEPSEEK_API_KEY，可留空"
                                     if _has_env_key
                                     else "sk-... 或设置 DEEPSEEK_API_KEY 环境变量"),
                    )
                    model = gr.Dropdown(
                        choices=["deepseek-chat", "deepseek-reasoner"],
                        value="deepseek-chat",
                        label="翻译模型",
                    )
                    chunk_size = gr.Slider(
                        minimum=500, maximum=5000, value=3000, step=100,
                        label="单块最大字符数",
                    )
                    workers = gr.Slider(
                        minimum=1, maximum=32, value=8, step=1,
                        label="翻译并发数（1=串行，8 默认，大于 DeepSeek 配额会被限流）",
                    )

                run_btn = gr.Button("▶ 开始批量处理", variant="primary", size="lg")

                with gr.Accordion("mineru-api 模型服务", open=False):
                    api_status = gr.Textbox(
                        label="状态",
                        value=ui_status_api(),
                        interactive=False,
                        lines=3,
                    )
                    with gr.Row():
                        btn_status = gr.Button("🔄 刷新")
                        btn_start = gr.Button("▶ 预热")
                        btn_stop = gr.Button("■ 停止")
                    btn_status.click(fn=ui_status_api, outputs=[api_status])
                    btn_start.click(fn=ui_start_api, outputs=[api_status])
                    btn_stop.click(fn=ui_stop_api, outputs=[api_status])

            with gr.Column(scale=2, min_width=520):
                gr.Markdown("### 进度")
                log_box = gr.Textbox(
                    label="日志",
                    lines=24,
                    max_lines=60,
                    autoscroll=True,
                    interactive=False,
                )
                gr.Markdown("### 输出文件")
                en_files = gr.File(
                    label="英文 Markdown（已完成）",
                    interactive=False,
                    file_count="multiple",
                )
                zh_files = gr.File(
                    label="中文 Markdown（已完成）",
                    interactive=False,
                    file_count="multiple",
                )

        run_btn.click(
            fn=run_workflow,
            inputs=[
                pdf_input, md_input,
                do_convert, do_translate, reuse_server,
                backend, lang, start_page, end_page,
                model, chunk_size, workers, api_key,
            ],
            outputs=[log_box, en_files, zh_files],
        )

        gr.Markdown(
            f"输出根目录：`{OUTPUT_ROOT}`\n\n"
            "复用模式下，mineru-api 在首次批处理时启动（约 1 分钟加载模型），"
            "之后每个 PDF 都走 HTTP 提交、无重复加载开销。关闭 Gradio 时服务自动结束。"
        )

    return app


if __name__ == "__main__":
    ui = build_ui()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    ui.queue().launch(
        inbrowser=True,
        server_name="127.0.0.1",
        server_port=7860,
        theme=gr.themes.Soft(),
        allowed_paths=[str(OUTPUT_ROOT)],
    )
