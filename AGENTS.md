# Blueprint Studio Backend

Public product name: Blueprint Studio Backend. Chinese name: 藍圖工作室（後台）.
Default new workspace name: 藍圖工作室 Blueprint Studio. The product is an
extensible entrepreneurship backend; ship only explicitly requested core features.
Keep existing CLI/module identifiers, localStorage keys, markers, databases and
Docker volume names compatible. Preserve existing user-chosen workspace paths.

This repository includes a local-first folder tool and a separately stored company
library. Keep the initial folder purpose optional and blank; Codex supplies the
reasoning when the user asks. The user's expanded scope authorizes a persistent
company backend. Its current scope is file organization and website/YouTube
collections only. Do not add unrelated company features or OS icon hooks.

- `blueprint_folders/`: Python 3.10+ standard-library CLI for actual folders.
- `web/`: dependency-free browser UI, including phone layouts.
- `dist/index.html`: generated, standalone offline browser build.
- `assets/icons/`: consistent portable SVG/PNG artwork.
- `blueprint_library/`: authenticated HTTP API, external SQLite data and immutable
  uploaded files. Never save company databases, credentials or uploads in Git.
- `library_web/`: functional company-library interface. The user will provide a
  visual design direction separately; keep this distinct from the legacy UI.
- Automatic categories currently use explicit local metadata/keyword rules.
  Describe these truthfully; there is no connected language model or silent AI
  access to conversations, YouTube transcripts or external page contents.
- Folder identity lives in `.blueprint/folder.json`; preserve unknown fields.
- `link` binds an external private studio to this shared tool checkout. Keep
  studios outside this repository; `blueprint-studio.py` delegates to the current
  shared code. Preserve studio source, guidance and binding extensions on relink.
- Publish reusable tool changes here. Keep private studio projects and personal
  instructions in their own directories; do not stage them into this repository.
- Preserve existing source files and `AGENTS.md` when adding markers.
- Browser storage belongs to that browser; use ZIP/JSON for transfer and backup.
- After editing web source, run `python tools/build_web.py`.
- Verify with `python -m unittest discover -s tests -v` and
  `node --test tests/test_packager.cjs`.
