"""Credential and endpoint resolution.

Resolution order, highest first:

1. explicit CLI argument
2. environment variable
3. ``PDF2MD_SECRETS`` file (a JSON path the user chooses)
4. ``./.secrets/pdf2md.json`` (project-local)
5. ``~/.config/pdf2md/secrets.json`` (user-level, works after installing the skill)
6. ``./.env`` (``KEY=VALUE``; parsed, never exported into ``os.environ``)

Secrets files are JSON. Every field is optional::

    {"mineru_token": "...", "base_url": "...", "model": "...", "api_key": "..."}

Nothing here ever prints a secret in full; :meth:`Value.masked` is the only
sanctioned text form.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from errors import ConfigError

__all__ = [
    "ConfigError",
    "Settings",
    "Value",
    "load_settings",
    "missing_message",
    "read_env_file",
    "secret_files",
]

#: accepted environment variable names per field, in order
FIELD_ENV: dict[str, tuple[str, ...]] = {
    "mineru_token": ("MINERU_TOKEN", "PDF2MD_MINERU_TOKEN"),
    "base_url": ("PDF2MD_BASE_URL", "OPENAI_BASE_URL"),
    "model": ("PDF2MD_MODEL",),
    "api_key": ("PDF2MD_API_KEY", "OPENAI_API_KEY"),
}

#: translation fields that must all resolve before a run may translate
TRANSLATION_FIELDS = ("base_url", "model", "api_key")


@dataclass(frozen=True)
class Value:
    """A resolved setting plus the place it came from."""

    text: str
    source: str

    def masked(self) -> str:
        text = self.text
        if len(text) <= 12:
            return f"***（len={len(text)}）"
        return f"{text[:6]}…{text[-4:]}（len={len(text)}）"


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from ``.env``; comments and blanks ignored."""
    target = path or Path.cwd() / ".env"
    if not target.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def secret_files() -> list[Path]:
    """Candidate JSON files, in priority order. Missing files are fine."""
    candidates: list[Path] = []
    override = os.environ.get("PDF2MD_SECRETS", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path.cwd() / ".secrets" / "pdf2md.json")
    candidates.append(Path.home() / ".config" / "pdf2md" / "secrets.json")
    return candidates


def _from_files(field: str) -> Value | None:
    for path in secret_files():
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        value = str(payload.get(field, "")).strip()
        if value:
            return Value(value, str(path))
    return None


def _from_env(field: str) -> Value | None:
    for name in FIELD_ENV.get(field, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return Value(value, f"环境变量 {name}")
    return None


def _from_env_file(field: str, env_file: dict[str, str]) -> Value | None:
    for name in FIELD_ENV.get(field, ()):
        value = env_file.get(name, "").strip()
        if value:
            return Value(value, f".env {name}")
    return None


def resolve(field: str, explicit: str | None = None) -> Value | None:
    """Resolve one field through the documented chain."""
    if (explicit or "").strip():
        return Value(explicit.strip(), "命令行参数")
    return _from_env(field) or _from_files(field) or _from_env_file(field, read_env_file())


@dataclass(frozen=True)
class Settings:
    mineru_token: Value | None = None
    base_url: Value | None = None
    model: Value | None = None
    api_key: Value | None = None

    def translation(self) -> tuple[Value, Value, Value]:
        """All three translation values, or a ConfigError naming what is absent."""
        absent = [name for name in TRANSLATION_FIELDS if getattr(self, name) is None]
        if absent:
            raise ConfigError(missing_message(absent))
        return self.base_url, self.model, self.api_key  # type: ignore[return-value]

    def require_mineru(self) -> Value:
        if self.mineru_token is None:
            raise ConfigError(
                "缺少 MinerU Token：精准解析（保留图片）必须有 Token。\n"
                "  1) 申请：https://mineru.net/apiManage/token\n"
                "  2) 写入 ./.secrets/pdf2md.json 的 mineru_token 字段\n"
                "  3) 或导出环境变量 MINERU_TOKEN\n"
                "  4) 或传 --mineru-token\n"
                "不愿申请 Token 时可加 --ocr flash：只有纯 Markdown，没有图片和结构 JSON。"
            )
        return self.mineru_token

    def describe(self) -> list[tuple[str, str, str]]:
        """(state, field, detail) rows for the doctor command."""
        rows: list[tuple[str, str, str]] = []
        for field, label in (("mineru_token", "MinerU Token"),
                             ("base_url", "翻译 Base URL"),
                             ("model", "翻译模型"),
                             ("api_key", "翻译 Key")):
            value: Value | None = getattr(self, field)
            if value is None:
                rows.append(("missing", label, "未配置"))
            else:
                shown = value.masked() if field in {"mineru_token", "api_key"} else value.text
                rows.append(("ok", label, f"{shown}  ← {value.source}"))
        return rows


def load_settings(*, mineru_token: str | None = None, base_url: str | None = None,
                  model: str | None = None, api_key: str | None = None) -> Settings:
    """Resolve every field once, honouring explicit overrides."""
    return Settings(
        mineru_token=resolve("mineru_token", mineru_token),
        base_url=resolve("base_url", base_url),
        model=resolve("model", model),
        api_key=resolve("api_key", api_key),
    )


def missing_message(absent: list[str]) -> str:
    labels = {"base_url": "Base URL", "model": "模型名", "api_key": "API Key"}
    names = "、".join(labels.get(name, name) for name in absent)
    return (
        f"翻译需要 {names}，但都没有解析到。任选一种方式提供：\n"
        "  1) 命令行：--base-url URL --model NAME --api-key KEY\n"
        "  2) 环境变量：PDF2MD_BASE_URL / PDF2MD_MODEL / PDF2MD_API_KEY\n"
        "  3) 写入 ./.secrets/pdf2md.json（或 ~/.config/pdf2md/secrets.json）：\n"
        '     {"base_url": "...", "model": "...", "api_key": "..."}\n'
        "  4) 写入 ./.env（KEY=VALUE）\n"
        "只想 OCR 不翻译时，去掉 --translate。"
    )
