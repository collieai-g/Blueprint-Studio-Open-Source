#!/usr/bin/env python3
"""Validate connection scripts using isolated CLI fixtures, never user config.

The native Codex MCP handshake is a separate real-client acceptance check.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CodexConnection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="blueprint-codex-connect-")
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo with spaces"
        self.scripts = self.repo / "scripts/v2"
        self.scripts.mkdir(parents=True)
        source = Path(__file__).resolve().parents[2] / "scripts/v2"
        for name in ["connect-codex.sh", "mcp-stdio.sh", "stack.sh", "mcp-stdio.ps1", "stack.ps1"]:
            shutil.copy2(source / name, self.scripts / name)
        self.private = self.root / 'private "quoted" directory'
        self.private.mkdir(mode=0o700)
        (self.private / ".env").write_text("COMPOSE_PROJECT_NAME=fixture\nBLUEPRINT_APP_IMAGE=blueprint:private\n")
        (self.private / "codex-token").write_text("disposable-fixture-token\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "mcp-state.json"
        self.state.write_text("{}")
        self.log = self.root / "mutations.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], FIXTURE_STATE=str(self.state), FIXTURE_LOG=str(self.log))
        self.executable("codex", r'''#!/usr/bin/env python3
import json, os, sys, tomllib
from pathlib import Path
args=sys.argv[1:]; overrides=[]
while args and args[0]=='-c': overrides.append(args[1]); args=args[2:]
state_path=Path(os.environ['FIXTURE_STATE']); state=json.loads(state_path.read_text())
if args[:2]==['mcp','list']:
 print(json.dumps(list(state.values())));sys.exit(0)
name=args[2] if len(args)>2 else ''
if args[:2]==['mcp','get']:
 if name not in state: sys.exit(1)
 item=state[name]
 for value in overrides:
  parsed=tomllib.loads(value)['mcp_servers'][name]
  for key,data in parsed.items():
   if key=='enabled': item[key]=data
   else: item['transport'][key]=data
 print(json.dumps(item,indent=2));sys.exit(0)
if args[:2]==['mcp','add']:
 at=args.index('--'); command=args[at+1]; command_args=args[at+2:]
 state[name]={'name':name,'enabled':True,'transport':{'type':'stdio','command':command,'args':command_args}}
 state_path.write_text(json.dumps(state))
 with open(os.environ['FIXTURE_LOG'],'a') as log: log.write(json.dumps(args)+'\n')
 print('Added MCP server '+name);sys.exit(0)
sys.exit(2)
''')
        self.executable("docker", r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
arguments=sys.argv[1:]
private_file=Path(arguments[arguments.index('--env-file')+1])
private=dict(line.split('=',1) for line in private_file.read_text().splitlines() if '=' in line)
Path(os.environ['FIXTURE_LOG']).write_text(json.dumps({'args':arguments,'token_present':os.environ.get('BLUEPRINT_MCP_TOKEN')=='disposable-fixture-token','company_variables':sorted(name for name in os.environ if name.startswith(('BLUEPRINT_','COMPOSE_','DOCUMENSO_','KEYCLOAK_','COLLABORA_','SMTP_')) or name in ('POSTGRES_PASSWORD','RUST_LOG')),'selected_project':os.environ.get('COMPOSE_PROJECT_NAME',private['COMPOSE_PROJECT_NAME']),'selected_image':os.environ.get('BLUEPRINT_APP_IMAGE',private['BLUEPRINT_APP_IMAGE']),'docker_context_preserved':os.environ.get('DOCKER_CONTEXT')=='fixture-context','proxy_preserved':os.environ.get('HTTP_PROXY')=='http://fixture-proxy.invalid:8080'}))
for line in sys.stdin:
 value=json.loads(line)
 if 'id' in value: print(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':{}}),flush=True)
''')

    def tearDown(self):
        self.directory.cleanup()

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o700)

    def connect(self, *args):
        return subprocess.run([str(self.scripts / "connect-codex.sh"), "--private-dir", str(self.private), *args], env=self.env, text=True, capture_output=True, timeout=10)

    def test_registration_is_scoped_idempotent_and_preserves_different_config(self):
        first = self.connect("--name", "company-blueprint")
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = self.state.read_text()
        self.assertNotIn("disposable-fixture-token", saved + first.stdout + first.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        repeat = self.connect("--name", "company-blueprint")
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(self.state.read_text(), saved)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        state = json.loads(saved)
        state["company-blueprint"]["transport"]["command"] = "/another/command"
        self.state.write_text(json.dumps(state))
        different = self.state.read_text()
        refused = self.connect("--name", "company-blueprint")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("preserved", refused.stderr)
        self.assertEqual(self.state.read_text(), different)

    def test_missing_token_does_not_register_or_overwrite(self):
        (self.private / "codex-token").unlink()
        result = self.connect()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_text(), "{}")
        self.assertFalse(self.log.exists())

    def test_stdio_forwards_rpc_only_and_passes_token_by_environment_name(self):
        (self.private / ".blueprint-install-profile").write_text("enterprise\n")
        result = subprocess.run([str(self.scripts / "mcp-stdio.sh"), "--private-dir", str(self.private)], env=self.env, input='{"jsonrpc":"2.0","id":"probe","method":"ping"}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"jsonrpc": "2.0", "id": "probe", "result": {}})
        operation = json.loads(self.log.read_text())
        self.assertTrue(operation["token_present"])
        self.assertNotIn("disposable-fixture-token", json.dumps(operation["args"]) + result.stdout + result.stderr)
        self.assertIn("BLUEPRINT_MCP_TOKEN", operation["args"])
        self.assertIn("enterprise", operation["args"])
        self.assertIn("-T", operation["args"])

    def hostile_environment(self):
        return dict(self.env, COMPOSE_PROJECT_NAME="wrong-company", COMPOSE_PROFILES="enterprise", COMPOSE_FILE="wrong-company.yaml", COMPOSE_ENV_FILES="wrong-company.env", COMPOSE_REMOVE_ORPHANS="1", BLUEPRINT_APP_IMAGE="wrong-company:image", BLUEPRINT_PUBLIC_URL="https://wrong-company.invalid", BLUEPRINT_MCP_TOKEN="inherited-wrong-token", POSTGRES_PASSWORD="wrong-fixture-password", DOCUMENSO_DB_PASSWORD="wrong-fixture-password", KEYCLOAK_DB_PASSWORD="wrong-fixture-password", COLLABORA_ADMIN_PASSWORD="wrong-fixture-password", SMTP_PASSWORD="wrong-fixture-password", RUST_LOG="trace", DOCKER_CONTEXT="fixture-context", HTTP_PROXY="http://fixture-proxy.invalid:8080")

    def assert_isolated_company(self, result, parent):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "isolated")
        operation = json.loads(self.log.read_text())
        self.assertEqual(operation["company_variables"], ["BLUEPRINT_MCP_TOKEN"])
        self.assertEqual(operation["selected_project"], "fixture")
        self.assertEqual(operation["selected_image"], "blueprint:private")
        self.assertTrue(operation["token_present"])
        self.assertTrue(operation["docker_context_preserved"])
        self.assertTrue(operation["proxy_preserved"])
        self.assertEqual(parent["COMPOSE_PROJECT_NAME"], "wrong-company")
        self.assertEqual(parent["BLUEPRINT_MCP_TOKEN"], "inherited-wrong-token")
        self.assertEqual(parent["BLUEPRINT_APP_IMAGE"], "wrong-company:image")
        self.assertNotIn("disposable-fixture-token", result.stdout + result.stderr)

    def test_stdio_selects_private_company_despite_inherited_environment(self):
        parent = self.hostile_environment()
        result = subprocess.run([str(self.scripts / "mcp-stdio.sh"), "--private-dir", str(self.private)], env=parent, input='{"jsonrpc":"2.0","id":"isolated","method":"ping"}\n', capture_output=True, text=True, timeout=10)
        self.assert_isolated_company(result, parent)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell runtime not installed")
    def test_powershell_stdio_selects_private_company_despite_inherited_environment(self):
        parent = self.hostile_environment()
        result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.scripts / "mcp-stdio.ps1"), "-PrivateDirectory", str(self.private)], env=parent, input='{"jsonrpc":"2.0","id":"isolated","method":"ping"}\n', capture_output=True, text=True, timeout=10)
        self.assert_isolated_company(result, parent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
