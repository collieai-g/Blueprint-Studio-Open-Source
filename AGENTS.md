# Blueprint Studio Open Source

Public repository name: Blueprint Studio Open Source (藍圖工作室開源).
Backend product name: Blueprint Studio Backend. Chinese name: 藍圖工作室（後台）.
Default new workspace name: 藍圖工作室 Blueprint Studio. The product is an
extensible entrepreneurship backend; ship only explicitly requested core features.
Keep existing CLI/module identifiers, localStorage keys, markers, databases and
Docker volume names compatible. Preserve existing user-chosen workspace paths.

This repository includes a local-first folder tool and a separately stored company
library. Keep the initial folder purpose optional and blank; the connected AI supplies the
reasoning when the user asks. The user's expanded scope authorizes a persistent
company backend. Its core scope is file organization and website/YouTube
collections. The user now authorizes a minimal workspace UI with practical
navigation, favourites, unfiled items, and explicit selected-source AI handoff.
The user also authorizes daily work records and checkable todos. The current AI
curates its visible conversation into useful outcomes, decisions, references and
unfinished tasks; the backend stores the curated result. Keep this distinct from
automatic access to other AI platforms or an unconfigured summarization model.
Installation has two explicit routes: first choose local or cloud, then ask the
workspace/company name; other profile fields are optional. Follow `docs/INSTALL.md`
and the selected `docs/INSTALL_LOCAL.md` or `docs/INSTALL_CLOUD.md` from the first
prompt through a usable original workbench. Reuse existing choices and resources.
Guide required GitHub account/repository setup, complete source import, Codex cloud
connection and actual hosting as separate cloud stages. Environment Publish is
development readiness; continue to a real authenticated, persistent application.
Tool-capable AI agents complete authorized work; for user-only login, verification
or platform buttons, give the exact next action and resume. Chat-only tools guide
users without claiming to execute commands. Keep setup and AI-maintained
INSTALL_PROGRESS.md outside this checkout; resume rather than reinitialize.
Preserve the original library_web HTML/CSS/JS, icons, layout and all existing
features during installation, including Sites adaptations. Do not generate a new
layout, reduce navigation or substitute a demo. gui/serve/dist are legacy tools;
the original backend uses library or company/Dockerfile. Sites requires an actual
compatible backend adaptation; this repository has no ready Sites deployment or
setup --provider sites. Verify the complete original feature checklist in
docs/INSTALL.md and report workbench readiness separately from verified AI writes.
The user now authorizes the V2 source redesign: AI-first entry with manual fallback,
white/gray notebook grid, the original Blueprint Studio Identity 05 logo, and
movable/resizable/collapsible dashboard cards. Installers preserve the selected
version's full functionality. Keep legacy APIs and schema 4 compatible; never
reinitialize existing installations on update. Follow docs/UPGRADE.md for full
private backups, same-site/source upgrades, data-preservation checks and rollback.
The V2 UI package can update a compatible Sites adapter; preserve its authentication,
API implementation, database, object storage and tool binding. Do not replace it
with native Python code or create another Site. AI writes use real existing tools;
UI polling is not permission to read conversations or a simulated AI connection.
Company profiles and versioned daily-work refinements are in scope. Preserve
completed todo state and retain history when useful records are edited or noise
is removed. Cloud bindings must use the authenticated remote API, not another
local SQLite database. Preparing a plan never means a service is deployed.
Do not add unrelated company features, simulated AI connections or OS icon hooks.

- `blueprint_folders/`: Python 3.10+ standard-library CLI for actual folders.
- `web/`: dependency-free browser UI, including phone layouts.
- `dist/index.html`: generated, standalone offline browser build.
- `assets/icons/`: consistent portable SVG/PNG artwork.
- `blueprint_library/`: authenticated HTTP API, external SQLite data and immutable
  uploaded files. Never save company databases, credentials or uploads in Git.
- `library_web/`: minimalist company workspace UI, informed by Shape of AI's
  interaction patterns. Keep this distinct from the legacy offline UI.
- Automatic categories currently use explicit local metadata/keyword rules.
  Describe these truthfully; there is no connected language model or silent AI
  access to conversations, YouTube transcripts or external page contents.
- `workday-connect` links a private workspace to an existing company library;
  its `WORKDAY.md` tells the connected AI how to save a day through `workday-save`. Follow
  that private binding for daily-save requests. Do not save company records in
  this public source checkout or silently create a library at a guessed path.
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
