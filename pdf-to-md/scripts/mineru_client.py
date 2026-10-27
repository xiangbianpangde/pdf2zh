"""MinerU cloud parsing: the precision channel (token, figures) and flash (no token).

Channel facts that shape this module (see ``references/mineru-api.md``):

* precision returns a **zip** that contains ``full.md`` plus ``images/`` and the
  structure JSON — the only channel that can satisfy "keep the figures";
* flash returns a single Markdown URL and nothing else;
* both are async: submit, poll, download;
* pre-signed upload URLs must be used with a bare ``PUT`` (no extra headers).
"""
from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import netclient
from errors import OcrError

__all__ = ["MineruError", "ParseResult", "check_token", "flash_parse", "precision_parse"]

BASE = "https://mineru.net"
PRECISION = f"{BASE}/api/v4"
FLASH = f"{BASE}/api/v1/agent"

#: short operator hint per documented error code
ERROR_HINTS = {
    "A0202": "Token 错误：检查 Bearer 前缀与 Token 是否完整",
    "A0211": "Token 过期：到 mineru.net 重新创建",
    "-500": "传参错误：检查 Content-Type 与参数类型",
    "-10001": "MinerU 服务异常：稍后重试",
    "-10002": "请求参数错误：检查参数格式",
    "-60002": "文件格式识别失败：文件名需带正确后缀",
    "-60003": "文件读取失败：PDF 可能损坏",
    "-60004": "空文件",
    "-60005": "文件超过 200 MB",
    "-60006": "页数超过 200 页：用 --pages 指定范围",
    "-60009": "MinerU 任务队列已满：稍后重试",
    "-60012": "找不到任务",
    "-60013": "无权访问该任务：只能查自己提交的任务",
    "-60018": "当日解析任务数已达上限",
    "-30001": "文件超过轻量接口 10 MB 上限",
    "-30002": "轻量接口不支持该文件类型",
    "-30003": "页数超过轻量接口上限：改用精准解析或 --pages",
    "-30004": "请求参数错误",
}

Report = Callable[[str], None]


class MineruError(OcrError):
    """Parsing failed at submit, poll, download or unpack time."""


@dataclass(frozen=True)
class ParseResult:
    channel: str
    root: Path
    markdown: Path
    content_list: Path | None

def _request(url: str, **kwargs) -> netclient.Response:
    """Transport failures are OCR failures here, not crashes."""
    try:
        return netclient.request(url, **kwargs)
    except ConnectionError as exc:
        raise MineruError(f"网络请求失败：{exc}") from exc


def _upload(url: str, payload: bytes) -> int:
    try:
        return netclient.upload(url, payload)
    except ConnectionError as exc:
        raise MineruError(f"上传失败：{exc}") from exc


def _api_failure(response: netclient.Response, what: str) -> MineruError:
    try:
        payload = response.json()
    except ValueError:
        return MineruError(f"{what}失败：HTTP {response.status}，{response.text[:200]}")
    code = payload.get("code")
    message = str(payload.get("msg") or payload.get("message") or "").strip()
    hint = ERROR_HINTS.get(str(code), "")
    if not hint and "token" in message.lower():
        hint = "Token 无效或已过期：到 https://mineru.net/apiManage/token 重新获取"
    if code is None:
        detail = f"{what}失败：HTTP {response.status}「{message or response.text[:120]}」"
    else:
        detail = f"{what}失败：HTTP {response.status} code={code}「{message}」"
    return MineruError(f"{detail}（{hint}）" if hint else detail, code=code)


def _payload(response: netclient.Response, what: str) -> dict:
    """Unwrap ``{code, msg, data}``; any non-zero code is a hard failure."""
    if response.status != 200:
        raise _api_failure(response, what)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MineruError(f"{what}失败：{exc}") from exc
    code = payload.get("code")
    if code not in (0, "0"):
        raise _api_failure(response, what)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MineruError(f"{what}失败：响应缺少 data 对象（{response.text[:200]}）")
    return data


def _poll(deadline_seconds: float, interval: float, fetch: Callable[[], dict],
          describe: Callable[[dict], tuple[str, PageProgress | None]],
          report: Report | None) -> dict:
    """Poll until the terminal state arrives or the deadline passes."""
    started = time.monotonic()
    deadline = started + deadline_seconds
    while time.monotonic() < deadline:
        time.sleep(interval)
        data = fetch()
        state, progress = describe(data)
        elapsed = int(time.monotonic() - started)
        if progress is not None and report is not None:
            report(f"  {elapsed}s {state}（{progress.extracted}/{progress.total} 页）")
        elif report is not None:
            report(f"  {elapsed}s {state}")
        if state == "done":
            return data
        if state == "failed":
            raise MineruError(
                f"MinerU 解析失败：{data.get('err_msg') or '未给出原因'}"
                f"（state={state}）", code=data.get("err_code"))
    raise MineruError(f"等待 MinerU 结果超时（>{int(deadline_seconds)}s）：这是等待超时，不是解析失败")


@dataclass(frozen=True)
class PageProgress:
    extracted: int
    total: int


def _progress(data: dict) -> PageProgress | None:
    raw = data.get("extract_progress") or {}
    extracted, total = raw.get("extracted_pages"), raw.get("total_pages")
    if isinstance(extracted, int) and isinstance(total, int):
        return PageProgress(extracted, total)
    return None


def check_token(token: str) -> tuple[bool, str]:
    """Cheap auth probe: querying a non-existent batch separates bad token from bad task."""
    response = _request(f"{PRECISION}/extract-results/batch/pdf2md-token-probe",
                                 headers={"Authorization": f"Bearer {token}"}, retries=2)
    try:
        payload = response.json()
    except ValueError:
        return False, f"无法解析响应（HTTP {response.status}）"
    code = payload.get("code")
    if str(code) in {"A0202", "A0211"}:
        return False, f"{code}：{ERROR_HINTS.get(str(code), payload.get('msg', ''))}"
    if response.status == 200:
        return True, "Token 可用"
    return False, f"HTTP {response.status}：{payload.get('msg', '')}"


def precision_parse(pdf: str | Path, workdir: str | Path, token: str, *,
                    model: str = "vlm", language: str = "en",
                    pages: str | None = None, timeout: float = 1800.0,
                    interval: float = 5.0, report: Report | None = None) -> ParseResult:
    """Upload → poll → download the result zip → unpack. The only channel with figures."""
    source = Path(pdf)
    target_dir = Path(workdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}

    file_entry: dict[str, object] = {"name": source.name, "data_id": source.stem[:60]}
    if pages:
        file_entry["page_ranges"] = pages
    body: dict[str, object] = {
        "files": [file_entry],
        "model_version": model,
        "language": language,
        "enable_formula": True,
        "enable_table": True,
    }
    if report:
        report(f"  申请上传链接（model_version={model}, language={language}）")
    data = _payload(_request(f"{PRECISION}/file-urls/batch", method="POST",
                                      json_body=body, headers=headers, timeout=120),
                    "申请上传链接")
    batch_id = data.get("batch_id")
    urls = data.get("file_urls") or []
    if not batch_id or not urls:
        raise MineruError("申请上传链接失败：响应缺少 batch_id 或 file_urls")

    payload = source.read_bytes()
    if report:
        report(f"  上传 {source.name}（{len(payload) / 1048576:.1f} MB）")
    status = _upload(str(urls[0]), payload)
    if status not in (200, 201):
        raise MineruError(f"上传失败：HTTP {status}")

    def fetch() -> dict:
        return _payload(_request(f"{PRECISION}/extract-results/batch/{batch_id}",
                                          headers=headers, timeout=120), "查询批量结果")

    def describe(item: dict) -> tuple[str, PageProgress | None]:
        results = item.get("extract_result") or []
        match = next((row for row in results if row.get("file_name") == source.name),
                     results[0] if results else {})
        return str(match.get("state", "unknown")), _progress(match)

    if report:
        report(f"  batch_id={batch_id}，等待解析")
    finished = _poll(timeout, interval, fetch, describe, report)
    results = finished.get("extract_result") or []
    match = next((row for row in results if row.get("file_name") == source.name),
                 results[0] if results else {})
    zip_url = match.get("full_zip_url")
    if not zip_url:
        raise MineruError("解析完成但响应缺少 full_zip_url")

    if report:
        report("  下载并解包结果")
    response = _request(str(zip_url), timeout=300, retries=3)
    if response.status != 200:
        raise MineruError(f"下载结果包失败：HTTP {response.status}")
    _unpack(response.body, target_dir)

    markdown = _find_markdown(target_dir)
    return ParseResult(channel="precision", root=target_dir, markdown=markdown,
                       content_list=_find_content_list(target_dir))


def _unpack(body: bytes, target_dir: Path) -> None:
    import io

    root = target_dir.resolve()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if destination != root and root not in destination.parents:
                raise MineruError(f"结果包内含越界路径：{member.filename}")
        archive.extractall(target_dir)


def _candidates(root: Path, suffix: str) -> list[Path]:
    return sorted((path for path in root.rglob(f"*{suffix}") if path.is_file()),
                  key=lambda path: (len(path.parts), str(path)))


def _find_markdown(root: Path) -> Path:
    preferred = root / "full.md"
    if preferred.is_file():
        return preferred
    for name in ("full.txt",):
        candidate = root / name
        if candidate.is_file():
            return candidate
    matches = _candidates(root, ".md")
    if not matches:
        raise MineruError(f"解包结果里没有 Markdown：{root}")
    return matches[0]


def _find_content_list(root: Path) -> Path | None:
    matches = [path for path in root.rglob("*content_list*.json") if path.is_file()]
    if matches:
        return sorted(matches, key=lambda path: (len(path.parts), str(path)))[0]
    for name in ("layout.json", "middle.json"):
        for path in root.rglob(name):
            if path.is_file():
                return path
    return None


def flash_parse(pdf: str | Path, workdir: str | Path, *, language: str = "en",
                pages: str | None = None, timeout: float = 1800.0,
                interval: float = 4.0, report: Report | None = None) -> ParseResult:
    """Signed upload → poll → download Markdown. No token, no figures, no structure JSON."""
    source = Path(pdf)
    target_dir = Path(workdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if source.stat().st_size > 10 * 1048576:
        raise MineruError("flash 通道限 10 MB：请改用 --ocr precision")

    body: dict[str, object] = {"file_name": source.name, "language": language,
                               "enable_table": True, "enable_formula": True}
    if pages:
        body["page_range"] = pages
    if report:
        report(f"  申请签名上传地址（language={language}）")
    data = _payload(_request(f"{FLASH}/parse/file", method="POST", json_body=body,
                                      timeout=120), "申请上传链接")
    task_id, file_url = data.get("task_id"), data.get("file_url")
    if not task_id or not file_url:
        raise MineruError("申请上传链接失败：响应缺少 task_id 或 file_url")

    payload = source.read_bytes()
    if report:
        report(f"  上传 {source.name}（{len(payload) / 1048576:.1f} MB）")
    status = _upload(str(file_url), payload)
    if status not in (200, 201):
        raise MineruError(f"上传失败：HTTP {status}")

    def fetch() -> dict:
        return _payload(_request(f"{FLASH}/parse/{task_id}", timeout=120),
                        "查询解析结果")

    def describe(item: dict) -> tuple[str, PageProgress | None]:
        return str(item.get("state", "unknown")), None

    if report:
        report(f"  task_id={task_id}，等待解析")
    finished = _poll(timeout, interval, fetch, describe, report)
    markdown_url = finished.get("markdown_url")
    if not markdown_url:
        raise MineruError("解析完成但响应缺少 markdown_url")

    if report:
        report("  下载 Markdown")
    response = _request(str(markdown_url), timeout=180, retries=3)
    if response.status != 200:
        raise MineruError(f"下载 Markdown 失败：HTTP {response.status}")
    markdown = target_dir / f"{source.stem}.md"
    markdown.write_bytes(response.body)
    return ParseResult(channel="flash", root=target_dir, markdown=markdown, content_list=None)
