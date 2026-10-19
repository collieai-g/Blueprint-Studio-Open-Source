# Blueprint Folder Agent

This repository is a small, local-first folder tool. Keep the initial folder
purpose optional and blank; Codex supplies the reasoning when the user asks.
Do not add an embedded model, API key requirement, database, or OS icon hooks.

- `blueprint_folders/`: Python 3.10+ standard-library CLI for actual folders.
- `web/`: dependency-free browser UI, including phone layouts.
- `dist/index.html`: generated, standalone offline browser build.
- `assets/icons/`: consistent portable SVG/PNG artwork.
- Folder identity lives in `.blueprint/folder.json`; preserve unknown fields.
- Preserve existing source files and `AGENTS.md` when adding markers.
- Browser storage belongs to that browser; use ZIP/JSON for transfer and backup.
- After editing web source, run `python tools/build_web.py`.
- Verify with `python -m unittest discover -s tests -v` and
  `node --test tests/test_packager.cjs`.
