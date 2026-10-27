# Frozen Paper Translator runtime

The user explicitly requested freezing the audited translation runtime. The protected paths are:

- `src/pdf2zh/cli.py`
- `src/pdf2zh/core/pipeline.py`
- `src/pdf2zh/core/config.py`
- `src/pdf2zh/core/mineru.py`
- `src/pdf2zh/core/translate.py`
- `requirements.txt`
- `tests/test_translation_contract.py`
- this `AGENTS.md`

Translation tasks may pass CLI/tool parameters and use external settings, `.secrets/`, caches and output directories.

The paths above are the audited translation runtime; `src/pdf2zh/core/contract.py`, `src/pdf2zh/core/html.py`, `src/pdf2zh/webui.py` and the packaging/CI files are outside the freeze. They must not edit/replace these paths, alter the runtime environment/dependencies, remove immutable flags, or self-repair the implementation. If a call fails, report the failure and ask for explicit unlock/repair authorization. A request to translate a new PDF is not permission to change code.

Other existing backend modifications are outside this freeze; do not reset or reorganize them. The snapshot and unlock documentation are at `~/.config/pi-paper-translator/freeze/`. macOS `uchg` is an accidental-write guard, not protection against an owner intentionally unlocking files.
