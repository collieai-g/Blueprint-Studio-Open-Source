// Exercises the real installer/configuration/stack wrappers against a Docker
// executable double. No image is pulled, built or started by this test.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const source = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const temporary = mkdtempSync(join(tmpdir(), 'blueprint installer checks '));
const hash = text => createHash('sha256').update(text).digest('hex');
const utf8 = path => readFileSync(path, 'utf8');
const available = (command, args) => spawnSync(command, args, { encoding: 'utf8', timeout: 15_000 }).status === 0;
const runtimes = [];
if (process.platform !== 'win32' && available('bash', ['--version'])) runtimes.push('bash');
if (available('pwsh', ['-NoLogo', '-NoProfile', '-Command', 'if ($PSVersionTable.PSVersion -lt [version]"7.2" -or (!$IsWindows -and !([IO.File].GetMethods() | Where-Object Name -eq SetUnixFileMode))) { exit 1 }'])) runtimes.push('powershell');
assert(runtimes.length > 0, 'Installer checks need Bash with OpenSSL or PowerShell 7.2+.');

function fixture(runtime) {
  const base = join(temporary, runtime);
  const repo = join(base, 'program source with spaces');
  const bin = join(base, 'mock bin');
  const duplicateBin = join(base, 'second Docker on PATH');
  const scripts = join(repo, 'scripts', 'v2');
  mkdirSync(scripts, { recursive: true });
  mkdirSync(bin, { recursive: true });
  mkdirSync(duplicateBin, { recursive: true });
  for (const name of ['install.sh', 'configure.sh', 'stack.sh', 'install.ps1', 'configure.ps1', 'stack.ps1']) {
    copyFileSync(join(source, 'scripts', 'v2', name), join(scripts, name));
  }
  writeFileSync(join(repo, 'Dockerfile'), '# fixture: Docker must never read or build this\n');
  writeFileSync(join(repo, 'compose.yaml'), '# fixture: the Docker double checks arguments only\n');
  const log = join(base, 'docker-arguments.jsonl');
  const mockPath = join(bin, 'docker-mock.cjs');
  writeFileSync(mockPath, `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const mode = process.env.MOCK_DOCKER_FAIL;
fs.appendFileSync(process.env.MOCK_DOCKER_LOG, JSON.stringify({args,
  appOverridesPresent: Object.hasOwn(process.env, 'BLUEPRINT_MASTER_KEY'),
  composeOverridesPresent: Object.keys(process.env).some(name => name.startsWith('COMPOSE_'))
}) + '\\n');
const out = value => process.stdout.write(value + '\\n');
if (args[0] === 'info') {
  if (mode === 'daemon') process.exit(1);
  out(mode === 'windows' ? 'windows' : 'linux');
} else if (args[0] === 'buildx' && args[1] === 'version') {
  if (mode === 'buildx') process.exit(1);
  out('github.com/docker/buildx v0.30.0');
} else if (args[0] === 'compose' && args[1] === 'version') {
  out(mode === 'compose' ? '1.29.2' : '2.40.0');
} else if (args[0] === 'compose' && args.includes('--env-file') && args.includes('--project-directory')) {
  const action = args.find(arg => ['up', 'exec', 'ps'].includes(arg));
  if (action === 'up') {
    if (mode === 'up') process.exit(1);
    if (!args.includes('-d') || !args.includes('--build')) process.exit(2);
  } else if (action === 'ps') {
    out(mode === 'worker' ? 'app\\npostgres' : 'app\\npostgres\\nworker');
  } else if (action === 'exec' && args.includes('app') && args.includes('curl')) {
    if (args.at(-1).endsWith('/health')) {
      if (mode === 'health') process.exit(1);
      out('{"status":"ok","database":"ready"}');
    } else if (args.at(-1).endsWith('/api/v1/status')) {
      out(mode === 'status' ? '{}' : JSON.stringify({ initialized: process.env.MOCK_INITIALIZED === 'true' }));
    } else process.exit(2);
  } else process.exit(2);
} else process.exit(2);
`);
  chmodSync(mockPath, 0o755);
  if (process.platform === 'win32') {
    writeFileSync(join(bin, 'docker.cmd'), `@echo off\r\n"${process.execPath}" "${mockPath}" %*\r\n`);
    writeFileSync(join(duplicateBin, 'docker.cmd'), '@echo off\r\nexit /b 97\r\n');
  } else {
    copyFileSync(mockPath, join(bin, 'docker'));
    chmodSync(join(bin, 'docker'), 0o755);
    writeFileSync(join(duplicateBin, 'docker'), '#!/bin/sh\nexit 97\n');
    chmodSync(join(duplicateBin, 'docker'), 0o755);
  }
  // Selecting one application is essential: Get-Command can return every PATH
  // match. The later sentinel must never be joined into, or used as, a command.
  const env = { ...process.env, PATH: `${bin}${delimiter}${duplicateBin}${delimiter}${process.env.PATH}`, MOCK_DOCKER_LOG: log };
  delete env.CODEX_PROXY_CERT;
  for (const name of Object.keys(env)) {
    if (/^(BLUEPRINT_|DOCUMENSO_|KEYCLOAK_|COLLABORA_|SMTP_|COMPOSE_)/.test(name) || name === 'POSTGRES_PASSWORD') delete env[name];
  }
  const records = () => existsSync(log) ? utf8(log).trim().split('\n').filter(Boolean).map(JSON.parse) : [];
  function run(privateDir, { profile, failure, initialized, extraEnv = {} } = {}) {
    const args = runtime === 'bash' ? [join(scripts, 'install.sh')] : ['-NoLogo', '-NoProfile', '-File', join(scripts, 'install.ps1')];
    if (privateDir) args.push(runtime === 'bash' ? '--private-dir' : '-PrivateDirectory', privateDir);
    if (profile) args.push(runtime === 'bash' ? '--profile' : '-Profile', profile);
    return spawnSync(runtime === 'bash' ? 'bash' : 'pwsh', args, {
      cwd: base, env: { ...env, MOCK_DOCKER_FAIL: failure ?? '', MOCK_INITIALIZED: initialized ? 'true' : 'false', ...extraEnv }, encoding: 'utf8', timeout: 120_000,
    });
  }
  return { base, repo, env, records, run };
}
function assertSuccess(result, message) {
  // Configuration never appears in assertion operands or failure reports.
  assert(result.status === 0, `${message}: ${result.stderr || result.error?.message || 'installer returned an error'}`);
}
function assertPrivate(privateDir) {
  if (process.platform !== 'win32') {
    assert((statSync(privateDir).mode & 0o777) === 0o700, 'Private directory must have mode 0700.');
    assert((statSync(join(privateDir, '.env')).mode & 0o777) === 0o600, 'Private configuration must have mode 0600.');
    assert((statSync(join(privateDir, '.blueprint-install-profile')).mode & 0o777) === 0o600, 'Saved profile must have mode 0600.');
  }
}
function assertNoSecrets(result, configuration) {
  for (const line of configuration.split(/\r?\n/)) {
    const match = line.match(/^(?:POSTGRES_PASSWORD|BLUEPRINT_SETUP_TOKEN|BLUEPRINT_MASTER_KEY|[A-Z_]*(?:PASSWORD|SECRET|ENCRYPTION_KEY))="?([a-f0-9]{24,})"?$/);
    if (match) assert(!(result.stdout + result.stderr).includes(match[1]), 'Installer output exposed a generated secret.');
  }
}
let checks = 0;
try {
  for (const runtime of runtimes) {
    const f = fixture(runtime);
    for (const failure of ['daemon', 'windows', 'compose', 'buildx']) {
      const privateDir = join(f.base, `preflight ${failure}`);
      const result = f.run(privateDir, { failure });
      assert(result.status !== 0, `${runtime}: unavailable prerequisite must stop installation.`);
      assert(!existsSync(privateDir), 'Prerequisite failure must happen before creating configuration.');
      checks++;
    }
    const privateDir = join(f.base, 'blueprint-private');
    let result = f.run();
    assertSuccess(result, `${runtime}: fresh basic installation`);
    const configuration = utf8(join(privateDir, '.env'));
    const originalHash = hash(configuration);
    const project = configuration.match(/^COMPOSE_PROJECT_NAME="?(blueprint-[a-f0-9]{12})"?$/m)?.[1];
    assert(Boolean(project), 'Fresh installs need an independent Compose project identity.');
    assertPrivate(privateDir);
    assertNoSecrets(result, configuration);
    assert(result.stdout.includes('http://127.0.0.1:8787'), 'Installer must print the usable browser URL.');
    assert(result.stdout.includes('BLUEPRINT_SETUP_TOKEN'), 'First run must explain browser setup.');
    assert(utf8(join(privateDir, '.blueprint-install-profile')).trim() === 'basic', 'Basic must be the saved default profile.');
    checks++;

    result = f.run(privateDir, { initialized: true, extraEnv: { BLUEPRINT_MASTER_KEY: 'must-not-override-private-file', COMPOSE_PROJECT_NAME: 'wrong-company', COMPOSE_PROFILES: 'enterprise', COMPOSE_REMOVE_ORPHANS: 'true' } });
    assertSuccess(result, `${runtime}: repeated installation`);
    assert(hash(utf8(join(privateDir, '.env'))) === originalHash, 'A rerun must preserve the exact existing configuration.');
    assert(result.stdout.includes('Sign in to your existing company'), 'Initialized instances must open the sign-in flow.');
    assertNoSecrets(result, configuration);
    const invocations = f.records();
    const stackCalls = invocations.filter(record => record.args[0] === 'compose' && record.args.includes('--env-file'));
    assert(stackCalls.every(record => !record.appOverridesPresent && !record.composeOverridesPresent), 'Inherited app/Compose overrides must be removed only from stack children.');
    assert(stackCalls.every(record => record.args[record.args.indexOf('--env-file') + 1] === join(privateDir, '.env')), 'Paths containing spaces must remain single arguments.');
    assert(stackCalls.every(record => !record.args.includes('--profile')), 'A basic rerun must not activate enterprise services.');
    checks++;

    const independentDir = join(f.base, 'independent company');
    result = f.run(independentDir);
    assertSuccess(result, `${runtime}: separate company installation`);
    assert(!utf8(join(independentDir, '.env')).includes(project), 'Different private directories must not share Compose project volumes.');
    checks++;

    const insideDir = join(f.repo, 'must not contain secrets');
    result = f.run(insideDir);
    assert(result.status !== 0 && !existsSync(join(insideDir, '.env')), 'Private configuration inside the repository must be refused.');
    if (process.platform !== 'win32') {
      const linkedDir = join(f.base, 'linked source');
      symlinkSync(f.repo, linkedDir, 'dir');
      result = f.run(join(linkedDir, 'linked private'));
      assert(result.status !== 0 && !existsSync(join(f.repo, 'linked private', '.env')), 'A symlink into the checkout must not bypass private-directory checks.');
    }
    checks++;

    const manualDir = join(f.base, 'manual existing configuration');
    mkdirSync(manualDir);
    writeFileSync(join(manualDir, '.env'), configuration);
    result = f.run(manualDir);
    assert(result.status !== 0, 'Existing manual configurations need an explicit profile before first installer use.');
    assert(!existsSync(join(manualDir, '.blueprint-install-profile')), 'An ambiguous existing installation must not be assigned a profile.');
    result = f.run(manualDir, { profile: 'basic' });
    assertSuccess(result, `${runtime}: explicitly adopted manual installation`);
    assert(hash(utf8(join(manualDir, '.env'))) === originalHash, 'Adopting a manual installation must not change its secrets or project identity.');
    checks++;

    const documentsDir = join(f.base, 'documents failed startup');
    result = f.run(documentsDir, { profile: 'documents', failure: 'up' });
    assert(result.status !== 0, 'Docker startup errors must be reported.');
    const documentsHash = hash(utf8(join(documentsDir, '.env')));
    assert(utf8(join(documentsDir, '.blueprint-install-profile')).trim() === 'documents', 'A failed startup must preserve its chosen profile for recovery.');
    assertNoSecrets(result, utf8(join(documentsDir, '.env')));
    const beforeRetry = f.records().length;
    result = f.run(documentsDir);
    assertSuccess(result, `${runtime}: recovery with saved documents profile`);
    assert(hash(utf8(join(documentsDir, '.env'))) === documentsHash, 'Retry after startup failure must preserve configuration.');
    assert(f.records().slice(beforeRetry).filter(record => record.args.includes('--env-file')).every(record => record.args.includes('documents')), 'A default rerun must reuse the saved documents profile.');
    const beforeMismatch = f.records().filter(record => record.args.includes('--env-file')).length;
    result = f.run(documentsDir, { profile: 'enterprise' });
    assert(result.status !== 0, 'Changing storage profiles must stop before Docker startup.');
    assert(f.records().filter(record => record.args.includes('--env-file')).length === beforeMismatch, 'A profile mismatch must not mutate running services.');
    assert(hash(utf8(join(documentsDir, '.env'))) === documentsHash, 'A profile mismatch must preserve configuration.');
    checks++;

    for (const failure of ['health', 'worker', 'status']) {
      result = f.run(privateDir, { failure });
      assert(result.status !== 0, `${runtime}: unhealthy or incomplete installation must not report success.`);
      assert(hash(utf8(join(privateDir, '.env'))) === originalHash, 'Health failures must not replace configuration.');
      assert(!result.stdout.includes('Blueprint is ready'), 'An incomplete installation must not advertise readiness.');
      checks++;
    }
    assert(f.records().every(record => !record.args.some(argument => ['down', 'prune', 'rm', 'volume', '-v'].includes(argument))), 'Installer must never delete company containers, volumes or global Docker resources.');
    console.log(`${runtime}: installer safety and first-use checks passed.`);
  }
  console.log(`Installer checks passed: ${checks} cases; runtimes exercised: ${runtimes.join(', ')}. No Docker deployment was performed.`);
  if (!runtimes.includes('powershell')) console.log('PowerShell runtime unavailable here; the same suite exercises install.ps1 when pwsh 7.2+ is installed.');
} finally {
  // This removes only fixtures created by this process, never installation data.
  rmSync(temporary, { recursive: true, force: true });
}
