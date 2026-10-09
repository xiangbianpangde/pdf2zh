# Frozen Paper Translator runtime

The user explicitly requested freezing the audited translation runtime. The protected paths are:

- `pdf_to_zh_md.py`
- `pipeline.py`
- `config.py`
- `mineru_api.py`
- `translate_md.py`
- `requirements.txt`
- `tools/test_translation_contract.py`
- this `AGENTS.md`

Translation tasks may pass CLI/tool parameters and use external settings, `.secrets/`, caches and output directories. They must not edit/replace these paths, alter the runtime environment/dependencies, remove immutable flags, or self-repair the implementation. If a call fails, report the failure and ask for explicit unlock/repair authorization. A request to translate a new PDF is not permission to change code.

Other existing backend modifications are outside this freeze; do not reset or reorganize them. The snapshot and unlock documentation are at `~/.config/pi-paper-translator/freeze/`. macOS `uchg` is an accidental-write guard, not protection against an owner intentionally unlocking files.
