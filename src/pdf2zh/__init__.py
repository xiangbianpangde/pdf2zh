"""pdf2zh — English PDF to Chinese Markdown delivery pipeline.

One PDF in, one self-contained directory out: the Chinese Markdown, the English
Markdown, a copy of the source PDF and ``images/``.

Modules are import-only. Every runnable surface has its own entry point:

============================  ================  ===========================
console script                module            purpose
============================  ================  ===========================
``pdf2zh-pipeline``           ``pdf2zh.cli``    cloud OCR + translation CLI
``pdf2zh-translate``          ``pdf2zh.core.translate``  Markdown → Chinese
``pdf2zh-webui``              ``pdf2zh.webui``  local Gradio UI
============================  ================  ===========================
"""

from pathlib import Path

#: Repository root: ``src/pdf2zh/__init__.py`` -> ``src/pdf2zh`` -> ``src`` -> root.
#: ``.env``, ``.secrets/`` and the default ``./output`` are resolved against it,
#: so behaviour does not depend on the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

__all__ = ["PROJECT_ROOT"]
