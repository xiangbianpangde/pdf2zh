"""配置解析：把 .env / .secrets 收敛成一份确定的翻译后端配置。

设计原则
--------
1. **显式参数 > .secrets > 环境变量 > .env 文件 > 内置预设**
   .secrets 排在环境变量前面：环境变量里常残留已过期的 key（实测踩坑），
   .secrets/minimax.json 才是当前有效凭据。
2. 只读 .env，不写回 os.environ —— 避免污染同进程内的其它工具（如 app.py 的 UI）。
3. 所有凭据都不出现在日志/异常里，只打印来源和脱敏前缀。

用法：
    from config import resolve_translate
    cfg = resolve_translate()
    print(cfg.describe())
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
SECRETS_DIR = PROJECT_DIR / ".secrets"
# 共享凭据池（与每日早报项目共用同一份 key/mineru token）
SHARED_SECRETS_DIR = Path("/Users/xbpd/WorkBuddy/每日早报/.secrets")

# provider -> (默认 base_url, 默认 model, 读取 key 的环境变量名, 是否本地后端)
PRESETS: dict[str, dict] = {
    "minimax": {
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M3",
        "key_env": "MINIMAX_API_KEY",
        "base_env": "MINIMAX_BASE_URL",
        "model_env": "MINIMAX_MODEL",
        "local": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "base_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "local": False,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": None,  # openrouter 型号多，必须由 .env / 参数指定
        "key_env": "OPENROUTER_API_KEY",
        "base_env": "OPENROUTER_BASE_URL",
        "model_env": "OPENROUTER_MODEL",
        "local": False,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:14b",
        "key_env": "OLLAMA_API_KEY",
        "base_env": "OLLAMA_BASE_URL",
        "model_env": "OLLAMA_MODEL",
        "local": True,
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "model": None,
        "key_env": "LMSTUDIO_API_KEY",
        "base_env": "LMSTUDIO_BASE_URL",
        "model_env": None,
        "local": True,
    },
}
DEFAULT_PROVIDER = "minimax"


class ConfigError(RuntimeError):
    """配置不足以启动翻译。"""


@dataclass
class TranslateConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    key_source: str
    is_local: bool = False

    def describe(self) -> str:
        return (f"翻译后端: {self.provider} | model={self.model} | "
                f"base_url={self.base_url} | key 来源={self.key_source}")


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """解析 .env（只取 KEY=VALUE，跳过注释/空行），不写入 os.environ。"""
    p = path or ENV_FILE
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _secret_candidates(filename: str) -> list[Path]:
    return [SECRETS_DIR / filename, SHARED_SECRETS_DIR / filename]


def load_secret(filename: str, field: str) -> tuple[str, str]:
    """从 .secrets/<file>.json 读字段，返回 (值, 来源路径)；找不到返回 ("", "")。"""
    for p in _secret_candidates(filename):
        if not p.exists():
            continue
        try:
            val = str(json.loads(p.read_text(encoding="utf-8")).get(field, "")).strip()
        except (json.JSONDecodeError, OSError):
            continue
        if val:
            return val, str(p)
    return "", ""


def infer_provider(base_url: str | None) -> str | None:
    """从 base_url 反推 provider，用于 UI 里直接填了地址的情况。"""
    if not base_url:
        return None
    u = base_url.lower()
    if "minimaxi" in u or "minimax" in u:
        return "minimax"
    if "deepseek" in u:
        return "deepseek"
    if "openrouter" in u:
        return "openrouter"
    if ":11434" in u:
        return "ollama"
    if ":1234" in u:
        return "lmstudio"
    return None


def _mask(secret: str) -> str:
    if not secret:
        return "<empty>"
    return f"{secret[:9]}***(len={len(secret)})"


def resolve_translate(
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> TranslateConfig:
    """按 显式参数 > .secrets > 环境变量 > .env > 预设 的顺序拼出翻译配置。

    provider 为 None 时：先按 base_url 猜；猜不到用 DEFAULT_PROVIDER（minimax）。
    本地后端（ollama/lmstudio）没有真实 key 时填占位符 "local"——OpenAI SDK 拒绝空串。
    """
    envfile = read_env_file()
    prov = (provider or infer_provider(base_url) or DEFAULT_PROVIDER).lower()
    if prov not in PRESETS:
        prov = infer_provider(base_url) or DEFAULT_PROVIDER
    p = PRESETS[prov]

    # ── base_url ───────────────────────────────────────────────
    base = (base_url
            or os.getenv(p["base_env"], "").strip()
            or envfile.get(p["base_env"], "").strip()
            or p["base_url"])

    # ── model ──────────────────────────────────────────────────
    model_env = p["model_env"]
    mdl = (model or "").strip()
    if not mdl and model_env:
        mdl = os.getenv(model_env, "").strip() or envfile.get(model_env, "").strip()
    mdl = mdl or p["model"]
    if not mdl:
        raise ConfigError(f"{prov} 没有默认模型名，请显式传 --model（或在 .env 填 "
                          f"{p['model_env'] or '对应 *_MODEL'}）")

    # ── api_key ────────────────────────────────────────────────
    key = (api_key or "").strip()
    src = "显式参数"
    if not key and prov == "minimax":
        # 本地已配置好的 MiniMax 凭据（.secrets 优先于环境变量里的过期值）
        key, src = load_secret("minimax.json", "key") or ("", "")
    if not key:
        key = os.getenv(p["key_env"], "").strip()
        src = f"环境变量 {p['key_env']}" if key else ""
    if not key:
        key = envfile.get(p["key_env"], "").strip()
        src = f".env {p['key_env']}" if key else ""
    if not key and p["local"]:
        key, src = "local", "本地后端占位符"
    if not key:
        raise ConfigError(
            f"缺少 {prov} 的 API key。请任选其一：\n"
            f"  1) 写入 {SECRETS_DIR / 'minimax.json'}："
            f'{{"key": "sk-..."}}\n'
            f"  2) 设置环境变量 {p['key_env']}\n"
            f"  3) 在 .env 里填 {p['key_env']}=\n"
            f"  4) 命令行显式传 --api-key"
        )
    return TranslateConfig(provider=prov, base_url=base, model=mdl, api_key=key,
                           key_source=src, is_local=p["local"])


def load_mineru_token() -> str:
    """MinerU 云 API token：环境变量 > .secrets/mineru.json > 共享池。"""
    tok = os.getenv("MINERU_TOKEN", "").strip()
    if tok:
        return tok
    val, _ = load_secret("mineru.json", "token")
    return val


def doctor() -> list[tuple[bool, bool, str]]:
    """自检：逐项报告 (是否就绪, 是否默认管线必需, 说明)。"""
    rows: list[tuple[bool, bool, str]] = []
    tok, tok_src = load_secret("mineru.json", "token")
    if not tok:
        tok = os.getenv("MINERU_TOKEN", "").strip()
        tok_src = "环境变量 MINERU_TOKEN"
    rows.append((bool(tok), True,
                 f"MinerU 云 OCR token: {_mask(tok)}（{tok_src or '未配置'}）"
                 if tok else
                 "MinerU 云 OCR token: 未配置 — 见 https://mineru.net/apiManage/token"))
    for prov in ("minimax", "deepseek", "openrouter", "ollama", "lmstudio"):
        try:
            cfg = resolve_translate(provider=prov)
            rows.append((True, prov == DEFAULT_PROVIDER, f"{prov:10s} ✓ {cfg.describe()}"))
        except ConfigError as exc:
            rows.append((False, prov == DEFAULT_PROVIDER,
                         f"{prov:10s} ✗ {str(exc).splitlines()[0]}"))
    return rows
