# Blueprint Studio Open Source

Honor the user's selected task before detecting or resuming an installation.
An explicit fresh installation that leaves the old workbench untouched uses
INSTALL_AI.txt, even when an old Site or unfinished upgrade exists. Record the
old target as protected, create a separate private plan, data path, backup path
and service, and never reuse its Site ID, volume, credentials or MCP binding.
Do not make old D1/R2 backups, candidate recovery or old-site publishing a gate
for an independent fresh native installation. Do not import old data unless
requested. If the user wants an in-place update or data migration, preserve the
corresponding backup requirements; never silently substitute a blank new site.
Read existing choices before asking. A fresh install is not permission to erase
the old installation, and its completion must not be reported as an old-site update.

V2.5 makes native local/cloud lifecycle the primary fresh-install route. Follow
docs/NATIVE_LIFECYCLE.md before claiming installation is complete: persistent data
and backup paths, real backup download and isolated authenticated restore,
restart persistence, supported host redeployment, and actual MCP/UI acceptance.
serve_library uses Maintenance before Store can migrate live data. Preserve the
OS data lease, startup journal, rehearsal, previous rows/attachments/credentials,
and failed-data copies. Never initialize a nonempty directory or roll back code
by silently lowering a data version. Runtime backup pauses/drains HTTP requests,
not arbitrary SQLite writers; use one service and HTTP/MCP for active access.
Offline restore only targets a new directory. Native has 32 MCP tools; Sites keeps
the 30-tool shared contract and needs its own proven backup/restore adapter. Do not
promise automatic off-host copying, platform deployment or loaded chat tools from
native maintenance status. Keep all private runtime files out of Git.

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
version's full functionality. Keep legacy APIs and all schema 4 records compatible; never
reinitialize existing installations on update. Follow docs/UPGRADE.md for full
private backups, same-site/source upgrades, data-preservation checks and rollback.
The V2 UI package can update a compatible Sites adapter; preserve its authentication,
API implementation, database, object storage and tool binding. Do not replace it
with native Python code or create another Site. AI writes use real existing tools;
UI polling is not permission to read conversations or a simulated AI connection.
The user requires model-independent AI writes through a real plugin/tool layer.
Follow docs/AI_CONNECTION.md; integrations/mcp/tools.json defines the shared tool
contract. Native MCP stdio bridges to the existing authenticated HTTP backend,
never a separate local database. Sites uses an authenticated MCP adapter to its
existing API, retaining platform identity and storage. Support Codex, Claude,
Gemini and other compatible MCP clients; do not hardcode Codex as workday source.
V2.2 includes an in-workbench AI connection center. Start with INSTALL_AI.txt and
START_HERE.md; complete docs/CONNECT.md after the selected install/upgrade route.
Ship connection.js, connection-guide.json and the Sites connection.mjs module.
Each new chat reads blueprint_connection_guide for client setup and work routing.
Merge its entrypoint and backend URL into supported private client instructions.
Use the wizard-provided challenge/client/date for blueprint_verify_connection;
it creates an explicitly labelled acceptance workday in the same existing store,
reads back and updates its test todo, then the UI independently checks that record.
Reuse the challenge on retries. Do not manufacture completion receipts via a
browser or direct API; do not claim a historical check proves a new chat is loaded.
Follow the six built-in troubleshooting routes, keep proxy/TLS and use supported
platform configuration for network changes. Guide necessary user login/actions,
then finish one real user task. No database reinitialization. V2.4 adds schema 5 tables as described below.
Installed plugins, tool discovery and a visible workbench are separate from a
verified write/readback. Check the actual tools loaded in the current chat.
V2.3 authorizes grid-aligned personalized layout of all dashboard modules.
Titles, subtitles and dates are borderless transparent text, including two/four-cell
sizes. Show their boundaries and layout controls only in edit mode. Content modules
retain cards and usable minimum sizes. Use clear standard written Chinese suitable
for an eleven-year-old reader without childish wording. Preserve the original logo,
white/gray design and pale 24px grid. Pages extend down only on an explicit add-page
click, maximum three. Keep browser preference key blueprint-workspace-v2; back up
legacy preferences before first save. Responsive viewing must not overwrite layout.
See docs/LAYOUT.md; verify geometry and real browser navigation, persistence and
migration with tests/test_workspace.cjs and tests/check_layout_browser.py.
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

V2.4 authorizes the five-group navigation and persistent company workflows in docs/V2.4.md.
Preserve the V2.3 visual style, grid and all old data. Additive schema 5 migration
creates studio tables transactionally, without resetting old records or credentials.
Old binaries cannot open schema 5; rollback requires the matching pre-upgrade full
backup, after preserving newer data. Follow docs/STUDIO_API.md for platform ports.
Enforce finance/personnel and linked-resource permissions on the server, including
old APIs, history, downloads, search and MCP. Specialists remain unconnected shells;
never fabricate replies, screenshots, metrics or external sending. Connection
registration and recorded budget flags are not a running provider integration.
Describe pending capture workers, native-only backend availability and Sites
adaptation truthfully. All new copy uses clear standard written Chinese.

V2.4.1 adds UPDATE_AI.txt, docs/UPDATE_ASSISTANT.md and tools/update_assistant.py.
Prefer the private resumable upgrade helper over repeatedly asking users to copy
settings or recreate an already prepared candidate. Ship library_web/update.js
and preserve the minimal UI. Keep source checkpoints, browser preferences and full
production backups distinct. Check current runtime observations and actual loaded
tools; a published environment draft, installed plugin, or local build is not
production acceptance. The helper performs no deployment and has no generic Sites
backend or R2 backup. Missing historical scripts require checking the current
Sites skill/workflow, not declaring every publishing route unavailable.
Never put private handoffs, source bundles, data backups or tool credentials here.

V2.4.2 adds tools/source_checkpoint.py and docs/SOURCE_CHECKPOINT.md. The public
repository must remain a clean, reusable product: never save private workbench
source, conversations, data, deployment settings, progress or handoffs here.
Use a separately verified private repository explicitly selected for source-only
checkpoints; never default to the current checkout's remote. Review contents,
exclude secrets/data/platform settings and keep production backups elsewhere.
Save a new checkpoint branch, read back the pinned remote commit and verify it
before declaring cross-environment source handoff ready. A local ZIP is not a
durable handoff. Do not confuse source recovery with deployment, attachment backup
or loaded MCP tools. When old source exists only in an inaccessible old environment,
state the boundary and provide one old-chat save instruction; do not claim recovery.

Follow docs/UPDATE_ROUTE.md through same-site production and real AI/UI acceptance
when the user's goal is an upgrade. A source-checkpoint subtask must not replace
that goal. Reuse a verified remote candidate; do not ask for the same handoff again.
Honor explicit save-only requests, and resume deployment when the user requests
the full upgrade. Separate target release, candidate version, live product version
and platform sequence number. Integrate an older candidate before deploying once.
Keep Sites publishing capabilities separate from Blueprint MCP tools: missing new
MCP tools in an old live version must not become a circular prerequisite for the
upgrade. Full production backup remains required. State exact missing backup
capabilities and supported paths checked; do not invent a backup button or mark
an attachment listing as a complete object backup. Report website deployment and
AI connection acceptance separately. All private progress stays outside this repo.
