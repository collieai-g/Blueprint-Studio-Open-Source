# Install Blueprint and connect your existing Codex

Blueprint is the company data and execution backend. You continue talking in Codex; the browser workspace displays saved files, customer/project records, notes, tasks, calendar and daily work. Codex supplies the intelligence through MCP, using its own existing sign-in. No embedded model or second AI subscription is required by this connection mode.

## A prompt you can paste into Codex

```text
Install and connect the open-source Blueprint Studio Backend:
https://github.com/collieai-g/blueprint-folder-agent
Use branch rewrite/rust-workspace-v2 and read INSTALL.md and AGENTS.md.

Inspect the available environment first. Use my chosen computer/server; do not
treat a temporary coding workspace as my permanent company server. Preserve
existing company databases, volumes, private directories and Codex settings.

Clone into a fresh directory if needed, detect the OS and Docker/Compose,
then use scripts/v2/install.sh or install.ps1. Do not silently reinstall system
software. If a prerequisite is missing, explain the exact required step.
Guide me through creating my company and owner account in the browser and
setting the company timezone. Then use the Codex collaboration page to issue
a dedicated credential; keep it in the private installation directory as
codex-token. Never print or commit passwords or tokens.

Run the matching connect-codex script, refresh MCP/open a new Codex conversation
if necessary, and verify actual Blueprint tool discovery. Test workspace_info
and a harmless context lookup before claiming connection success. Read
docs/CODEX-WORKSPACE.md for company tasks. Ask for my real first work item;
do not seed imaginary customers, AI employees or successful work.
```

An agent needs a terminal and access to the chosen machine to perform installation. A text-only chat can explain the same steps. First-time Docker/system installation or a cloud account may require the user's own interaction.

## Start the web workspace

The basic profile runs Rust app, worker and PostgreSQL. Existing configured instances are reused; installer reruns must not delete their volumes or reset credentials. First installation currently builds the source through Docker; there is no published prebuilt release image yet.

Clone without automatic CRLF conversion for Linux container scripts:

```sh
git clone --config core.autocrlf=false --branch rewrite/rust-workspace-v2 https://github.com/collieai-g/blueprint-folder-agent.git
cd blueprint-folder-agent
./scripts/v2/install.sh --private-dir ../blueprint-private
```

Windows uses Docker Desktop with Linux containers and PowerShell 7.2 or newer:

```powershell
git clone --config core.autocrlf=false --branch rewrite/rust-workspace-v2 https://github.com/collieai-g/blueprint-folder-agent.git
Set-Location blueprint-folder-agent
./scripts/v2/install.ps1 -PrivateDirectory ../blueprint-private
```

The installer prints the actual browser URL after health checks. Read `BLUEPRINT_SETUP_TOKEN` in your private `.env` locally and enter it in the first-run page; the installer does not print it. Create your company and owner password, then set the company timezone in workspace settings. Keep the private directory and persistent database/file volumes in your backup plan.

Local installation is used through a browser on that machine and stops when it is off. For an always-running company service, deploy to a persistent server and configure your actual HTTPS domain using [operations](docs/V2-OPERATIONS.md). GitHub stores the source; it does not run the Rust database/worker backend for you.

## Connect Codex without another model key

1. Open **Codex 協作** in Blueprint. Create a dedicated credential and download `codex-token`.
2. Move that file into the chosen private directory beside `.env`. On macOS/Linux use `chmod 600 ../blueprint-private/codex-token`. Do not put it in the repository or paste it into chat.
3. On the same computer as the Codex client and Docker installation, run:

```sh
./scripts/v2/connect-codex.sh --private-dir ../blueprint-private
```

Windows:

```powershell
./scripts/v2/connect-codex.ps1 -PrivateDirectory ../blueprint-private
```

The connection uses a host wrapper and a Rust stdio MCP bridge inside the running app container. Codex configuration contains the wrapper/private-directory paths, not the credential. Existing different MCP settings are preserved. `--name` / `-Name` can select another server name for another company installation.

Refresh MCP or start a new Codex conversation. Ask it to call `blueprint_workspace_info` and `blueprint_context`. An MCP discovery result proves the tools are connected; a real model turn and a saved/read-back result prove that the full work path runs. Do not treat a configured server name or a successful HTTP health check as that full proof.

If a managed Codex conversation cannot refresh its tool list, its existing terminal tool can still call the same authenticated bridge. For example, this sends an MCP request without putting the credential in the command:

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"blueprint_workspace_info","arguments":{}}}' | ./scripts/v2/mcp-stdio.sh --private-dir ../blueprint-private
```

This is terminal access to the real MCP server, not a claim that native tools have been hot-loaded. The current Codex can use returned company context and make subsequent tool calls without starting a second model process.

For an externally hosted backend and local Codex, use a reachable HTTPS MCP endpoint with Codex's native HTTP configuration or run the Rust bridge against that endpoint with a private token file. The Docker wrapper above is specifically for a Codex client that can access the installation's Docker daemon. Secrets remain on the machine running the client; a web page cannot edit that client's local configuration itself.

## Your first company task

Provide actual people, projects and history to your workspace. Then tell Codex what you plan to do. It should retrieve cited company records, clarify missing goals or ambiguous people, save your answers, create the appropriate work and report actual IDs/links. Registered external AI workers can receive explicitly selected sources; their real run status determines completion.

See [the operating playbook](docs/CODEX-WORKSPACE.md) and [validation](docs/V2-VALIDATION.md). Files and company history stay in Blueprint across Codex conversations. Codex does not automatically retain every earlier chat: useful facts and answers must be saved to the workspace, and future conversations retrieve those records.
