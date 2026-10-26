// Use the documented CUA Tab API, in an already authorized synthetic browser task.
// All credentials below are public fictional fixture values, never user secrets.
const USERNAME = 'synthetic-user';
const PASSWORD = 'synthetic-test-only-password';
const CASE_ID = 'blueprint-login-001';

async function prepare(tab, variant) {
  await tab.getAXState();
  const snapshot = await tab.playwright.domSnapshot();
  if (!snapshot.includes(`Synthetic login fixture / ${variant}`) ||
      !snapshot.includes('帳號') || !snapshot.includes('密碼')) {
    throw new Error('Not the expected isolated synthetic login fixture. No input was entered.');
  }
  const origin = await tab.playwright.evaluate(() => location.origin);
  const parsed = new URL(origin);
  if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !parsed.port) {
    throw new Error('The synthetic fixture must use an explicit loopback port.');
  }
  await tab.playwright.locator('#username').fill(USERNAME);
  await tab.playwright.locator('#password').fill(PASSWORD);
  await tab.playwright.getByRole('button', { name: '登入工作台', exact: true }).click();
  return origin;
}

async function observations(tab, origin) {
  await tab.goto(`${origin}/__fixture/status`);
  const result = await tab.playwright.evaluate(() => JSON.parse(document.body.innerText));
  if (result.synthetic !== true || result.case_id !== CASE_ID) {
    throw new Error('The page did not return this synthetic case observation.');
  }
  return result;
}

export async function runBrowserRegression({ brokenTab, fixedTab }) {
  const brokenOrigin = await prepare(brokenTab, 'broken');
  await brokenTab.playwright.getByText(/拒絕來自其他網站的操作/).waitFor({
    state: 'visible', timeoutMs: 10000,
  });
  await brokenTab.getAXState();
  const broken = await observations(brokenTab, brokenOrigin);
  const brokenBlocked = broken.variants.broken.last_login_status === 403 &&
    broken.variants.broken.origins.null >= 1 &&
    broken.variants.broken.login_successes === 0;
  if (!brokenBlocked) throw new Error('The old form policy did not reproduce the expected null-origin rejection.');

  const fixedOrigin = await prepare(fixedTab, 'fixed');
  if (fixedOrigin === brokenOrigin) throw new Error('Broken and fixed fixture targets must be independent.');
  await fixedTab.playwright.getByRole('heading', { name: '合成登入測試通過', exact: true }).waitFor({
    state: 'visible', timeoutMs: 10000,
  });
  await fixedTab.getAXState();
  const fixed = await observations(fixedTab, fixedOrigin);
  const fixedPassed = fixed.variants.fixed.login_successes >= 1 &&
    fixed.variants.fixed.origins.same >= 1 && fixed.backend_verified_requests >= 1;
  const nullRejected = fixed.null_origin_probe.passed === true &&
    fixed.null_origin_probe.backend_not_contacted === true;
  if (!fixedPassed || !nullRejected) throw new Error('The fixed login or separate null-origin security probe did not pass.');
  await fixedTab.goto(`${fixedOrigin}/`);
  await fixedTab.playwright.getByRole('heading', { name: '合成登入測試通過', exact: true }).waitFor({
    state: 'visible', timeoutMs: 10000,
  });
  return {
    contract_version: 1,
    kind: 'browser_form_regression',
    case_id: CASE_ID,
    synthetic: true,
    outcome: 'passed',
    checks: {
      broken_origin_blocked: brokenBlocked,
      fixed_login_passed: fixedPassed,
      null_origin_rejected: nullRejected,
    },
    mechanisms: {
      broken_origin_blocked: 'observed_browser_form_navigation',
      fixed_login_passed: 'observed_browser_form_navigation',
      null_origin_rejected: 'separate_synthetic_http_security_probe',
    },
    observations: fixed,
  };
}
