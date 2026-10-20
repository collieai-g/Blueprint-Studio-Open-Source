import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const temporary = mkdtempSync(join(tmpdir(), 'blueprint-deploy-test-'));
const privateDirectory = join(temporary, 'private');
const env = { ...process.env, COMPOSE_PROJECT_NAME: 'blueprint-deploy-check' };
// Rendering a Compose model does not need a build certificate or registration.
delete env.CODEX_PROXY_CERT;
const run = (command, args, extra = {}) => spawnSync(command, args, { cwd: root, env, encoding: 'utf8', ...extra });
try {
  let result = run('bash', ['scripts/v2/configure.sh', privateDirectory]);
  assert(result.status === 0, `Private configuration generation failed: ${result.stderr}`);
  const original = readFileSync(join(privateDirectory, '.env'), 'utf8');
  const values = Object.fromEntries(original.split('\n').filter(line => /^[A-Z][A-Z0-9_]*=/.test(line)).map(line => {
    const index = line.indexOf('=');
    return [line.slice(0, index), line.slice(index + 1).replace(/^"|"$/g, '')];
  }));
  for (const name of ['POSTGRES_PASSWORD', 'BLUEPRINT_SETUP_TOKEN', 'BLUEPRINT_MASTER_KEY']) {
    assert(/^[a-f0-9]{64}$/.test(values[name]), `${name} must be a generated 32-byte secret`);
    assert(!result.stdout.includes(values[name]), `${name} leaked to setup output`);
  }
  assert(new Set([values.POSTGRES_PASSWORD, values.BLUEPRINT_SETUP_TOKEN, values.BLUEPRINT_MASTER_KEY]).size === 3, 'Independent secrets must not be reused');
  if (process.platform !== 'win32') {
    assert((statSync(privateDirectory).mode & 0o777) === 0o700, 'Private directory must have mode 0700');
    assert((statSync(join(privateDirectory, '.env')).mode & 0o777) === 0o600, 'Private env must have mode 0600');
  }
  result = run('bash', ['scripts/v2/configure.sh', privateDirectory]);
  assert(result.status !== 0, 'A second setup must refuse to overwrite an existing installation');
  assert(readFileSync(join(privateDirectory, '.env'), 'utf8') === original, 'Existing configuration was changed');
  const s3 = JSON.parse(readFileSync(join(privateDirectory, 'secrets/s3.json'), 'utf8'));
  assert(s3.identities.length === 1 && !s3.identities.some(identity => identity.name === 'anonymous'), 'S3 must not allow anonymous access');
  assert(s3.identities[0].credentials[0].accessKey === values.BLUEPRINT_S3_ACCESS_KEY, 'S3 identity and app configuration differ');
  result = run('openssl', ['pkcs12', '-in', join(privateDirectory, 'secrets/signing.p12'), '-nokeys', '-passin', 'env:DOCUMENSO_SIGNING_PASSWORD'], {
    env: { ...env, DOCUMENSO_SIGNING_PASSWORD: values.DOCUMENSO_SIGNING_PASSWORD }
  });
  assert(result.status === 0 && result.stdout.includes('BEGIN CERTIFICATE'), 'Generated signing certificate is invalid');
  for (const profile of ['basic', 'documents', 'enterprise']) {
    result = run('bash', ['scripts/v2/stack.sh', privateDirectory, profile, 'config', '--format', 'json']);
    assert(result.status === 0, `${profile} Compose configuration failed: ${result.stderr}`);
    const model = JSON.parse(result.stdout);
    assert(model.services.app.command == null || model.services.app.command.includes('server'), 'App command must start the Rust server');
    assert(model.services.worker.command.includes('worker'), 'Worker must run the Rust worker');
    assert(model.services.app.environment.BLUEPRINT_MASTER_KEY === values.BLUEPRINT_MASTER_KEY, 'Persistent master key was not passed to app');
    assert(model.services.worker.environment.BLUEPRINT_MASTER_KEY === values.BLUEPRINT_MASTER_KEY, 'Server and worker keys differ');
    assert(model.services.app.ports.every(port => port.host_ip === '127.0.0.1'), 'Default app exposure must remain loopback');
    assert(!model.services.postgres.ports, 'PostgreSQL must not publish a host port');
    assert(!model.services.seaweed?.ports, 'S3 and internal storage administration must not publish host ports');
    for (const service of Object.values(model.services)) {
      if (service.image) assert(!/:latest(?:$|@)/.test(service.image), 'Image must use an explicit release tag');
    }
    if (profile !== 'basic') assert(model.services.app.environment.BLUEPRINT_TIKA_URL === 'http://tika:9998', 'Documents profile did not configure Tika');
    if (profile === 'enterprise') {
      assert(model.services.app.environment.BLUEPRINT_S3_ENDPOINT === 'http://seaweed:8333', 'Enterprise storage endpoint missing');
      assert(model.services.documenso.environment.NEXT_PRIVATE_DOCUMENSO_LICENSE_KEY === undefined, 'Free profile must not require an enterprise license');
      assert(model.services.documenso.environment.NEXT_PUBLIC_FEATURE_BILLING_ENABLED === 'false', 'Free profile must not enable billing');
    }
  }
  console.log('Deployment checks passed: private setup, overwrite refusal, certificate, and three Compose profiles.');
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
