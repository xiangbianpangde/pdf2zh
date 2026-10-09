"""MinerU 云 API OCR 模块（v4 Precision + Agent Flash）。

- precision：需 Token（https://mineru.net/apiManage/token 创建）
    流程：POST /api/v4/file-urls/batch 申请上传链接 → PUT 文件 → 系统自动建任务
         → 轮询 GET /api/v4/extract-results/batch/{batch_id} → 下载 full_zip_url 解包
- flash：免 Token（IP 限频，≤10MB / ≤20 页）
    流程：POST /api/v1/agent/parse/file → 轮询 → 下载 markdown_url

凭据查找顺序（token）：
    1. 环境变量 MINERU_TOKEN
    2. <本项目>/.secrets/mineru.json  的 "token" 字段
    3. /Users/xbpd/WorkBuddy/每日早报/.secrets/mineru.json（共享凭据池）

用法（作为库）：
    from mineru_api import precision_extract, flash_extract, MinerUError
    md_path = precision_extract(Path("paper.pdf"), Path("out/paper"))
"""
import http.client
import io
import json
import logging
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BASE = "https://mineru.net"
PROJECT_DIR = Path(__file__).resolve().parent
SECRETS_CANDIDATES = [
    PROJECT_DIR / ".secrets" / "mineru.json",
    Path("/Users/xbpd/WorkBuddy/每日早报/.secrets/mineru.json"),
]
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 1200  # 240 轮 × 5s


class MinerUError(RuntimeError):
    """MinerU 云 API 调用失败（提交/轮询/下载/解包任一环节）。"""


def load_token() -> str:
    """按 env > 本项目 .secrets > 共享 .secrets 顺序取 token。"""
    env_tok = __import__("os").environ.get("MINERU_TOKEN", "").strip()
    if env_tok:
        return env_tok
    for p in SECRETS_CANDIDATES:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("读取凭据 %s 失败: %s", p, exc)
            continue
        tok = str(data.get("token", "")).strip()
        if tok:
            return tok
    return ""


def _http(url: str, method: str = "GET", data=None, headers=None, timeout: int = 120, max_retries: int = 3):
    """轻量 HTTP：返回 (status, (content_type, body))；HTTPError 也返回而非抛出；自带重试。"""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {"Content-Type": "application/json"})
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, (resp.headers.get("Content-Type", ""), resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, (exc.headers.get("Content-Type", ""), exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_err = exc
            log.warning("HTTP 请求失败 (%s)，第 %d/%d 次重试...", exc, attempt + 1, max_retries)
            time.sleep(2 * (attempt + 1))
    raise MinerUError(f"HTTP 请求异常: {last_err}")


def _poll_batch(batch_id: str, headers: dict) -> str:
    """轮询批量任务直到 done，返回 full_zip_url；失败/超时抛 MinerUError。"""
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        status, resp = _http(f"{BASE}/api/v4/extract-results/batch/{batch_id}", headers=headers)
        try:
            item = json.loads(resp[1])["data"]["extract_result"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            log.debug("轮询响应异常（status=%s），继续等待", status)
            continue
        state = item.get("state")
        if state == "done":
            return item["full_zip_url"]
        if state == "failed":
            raise MinerUError(f"解析失败: {item.get('err_msg', 'unknown')}")
        log.debug("state=%s，继续等待", state)
    raise MinerUError(f"轮询超时（>{POLL_TIMEOUT_SEC}s），batch_id={batch_id}")


def precision_extract(pdf: Path, outdir: Path, model: str = "vlm") -> Path:
    """v4 Precision 解析：上传 → 轮询 → 下载 zip 解包到 outdir，返回 full.md 路径。

    model: "vlm"（默认，质量优先）或 "pipeline"
    """
    token = load_token()
    if not token:
        raise MinerUError("缺少 MinerU token：在 https://mineru.net/apiManage/token 创建，"
                          "写入 .secrets/mineru.json 的 token 字段或设置 MINERU_TOKEN")
    if not pdf.is_file():
        raise MinerUError(f"PDF 不存在: {pdf}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. 申请上传链接（上传后自动建任务）
    payload = json.dumps({"files": [{"name": pdf.name, "data_id": pdf.stem[:60]}],
                          "model_version": model}).encode()
    status, resp = _http(f"{BASE}/api/v4/file-urls/batch", "POST", payload, headers)
    if status != 200:
        raise MinerUError(f"申请上传链接失败 HTTP {status}: {resp[1][:300]!r}")
    body = json.loads(resp[1])
    if body.get("code") != 0:
        raise MinerUError(f"API 错误: {body}")
    batch_id = body["data"]["batch_id"]
    file_url = body["data"]["file_urls"][0]
    log.info("batch_id=%s，开始上传 %s（%.1f MB）", batch_id, pdf.name, pdf.stat().st_size / 1048576)

    # 2. PUT 直传 OSS——必须零额外头，否则签名不匹配(403)
    u = urlsplit(file_url)
    pdf_bytes = pdf.read_bytes()
    for attempt in range(3):
        try:
            conn = http.client.HTTPSConnection(u.hostname, timeout=300)
            conn.request("PUT", u.path + "?" + u.query, body=pdf_bytes)
            put_resp = conn.getresponse()
            put_resp.read()
            conn.close()
            if put_resp.status == 200:
                break
            if attempt == 2:
                raise MinerUError(f"上传失败 HTTP {put_resp.status}")
        except Exception as exc:
            if attempt == 2:
                raise MinerUError(f"上传异常: {exc}")
            time.sleep(2)
    log.info("上传完成，等待解析…")

    # 3. 轮询 + 4. 下载解包
    zip_url = _poll_batch(batch_id, headers)
    status, resp = _http(zip_url, timeout=300)
    if status != 200 or "zip" not in resp[0]:
        raise MinerUError(f"下载结果失败 HTTP {status}（Content-Type={resp[0]}）")
    outdir.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(resp[1])).extractall(outdir)
    md = outdir / "full.md"
    if not md.is_file():
        raise MinerUError(f"解包后未找到 full.md（{outdir} 内容异常）")
    log.info("OCR 完成 → %s（%d 字节）", md, md.stat().st_size)
    return md


def flash_extract(pdf: Path, outdir: Path) -> Path:
    """Agent Flash 解析（免 Token，≤10MB / ≤20 页）：multipart 上传 → 轮询 → 下载 md。"""
    import uuid

    if not pdf.is_file():
        raise MinerUError(f"PDF 不存在: {pdf}")
    if pdf.stat().st_size > 10 * 1048576:
        raise MinerUError("flash 模式限 ≤10MB，请用 precision 模式")

    boundary = uuid.uuid4().hex
    fname = pdf.name.encode("ascii", "ignore").decode() or "file.pdf"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_name\"\r\n\r\n"
            f"{pdf.name}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{fname}\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    multipart = head + pdf.read_bytes() + tail

    u = urlsplit(f"{BASE}/api/v1/agent/parse/file")
    conn = http.client.HTTPSConnection(u.hostname, timeout=300)
    conn.request("POST", u.path, body=multipart,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Content-Length": str(len(multipart))})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    if resp.status != 200:
        raise MinerUError(f"flash 提交失败 HTTP {resp.status}: {raw[:200]!r}")
    try:
        task_id = json.loads(raw)["data"]["task_id"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise MinerUError(f"flash 响应异常: {raw[:200]!r}") from exc
    log.info("flash task_id=%s，等待解析…", task_id)

    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        time.sleep(4)
        status, resp = _http(f"{BASE}/api/v1/agent/parse/{task_id}", timeout=120)
        try:
            data = json.loads(resp[1])["data"]
        except (json.JSONDecodeError, KeyError):
            log.debug("flash 轮询响应异常（status=%s），继续等待", status)
            continue
        if data.get("state") == "done":
            st2, resp2 = _http(data["markdown_url"], timeout=180)
            if st2 != 200:
                raise MinerUError(f"下载 markdown 失败 HTTP {st2}")
            outdir.mkdir(parents=True, exist_ok=True)
            md = outdir / f"{pdf.stem}.md"
            md.write_bytes(resp2[1])
            log.info("OCR 完成 → %s（%d 字节）", md, md.stat().st_size)
            return md
        if data.get("state") == "failed":
            raise MinerUError(f"flash 解析失败: {data.get('err_msg', 'unknown')}")
    raise MinerUError(f"flash 轮询超时（>{POLL_TIMEOUT_SEC}s），task_id={task_id}")
