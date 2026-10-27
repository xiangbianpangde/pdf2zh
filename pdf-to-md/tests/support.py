"""Shared test helpers: import path, synthetic images, credential isolation."""
from __future__ import annotations

import contextlib
import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def png_bytes(width: int = 8, height: int = 8, color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    """A real, decodable PNG built with the standard library only."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes(color) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@contextlib.contextmanager
def isolated_environment():
    """Run with no discoverable credentials and a throwaway working directory."""
    saved = {key: os.environ.get(key) for key in list(os.environ) if key.startswith("PDF2MD_")}
    for key in list(os.environ):
        if key.startswith("PDF2MD_") or key in {"MINERU_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            os.environ.pop(key, None)
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        previous_home, previous_cwd = os.environ.get("HOME"), os.getcwd()
        os.environ["HOME"] = home
        os.environ["PDF2MD_SECRETS"] = str(Path(home) / "absent.json")
        os.chdir(work)
        try:
            yield Path(work)
        finally:
            os.chdir(previous_cwd)
            if previous_home is not None:
                os.environ["HOME"] = previous_home
            else:
                os.environ.pop("HOME", None)
            os.environ.pop("PDF2MD_SECRETS", None)
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value
