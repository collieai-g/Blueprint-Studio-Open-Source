"""Signed release channels and bounded exact-version dependency resolution.

These functions plan metadata only. The installation controller still verifies
each selected artifact and performs each component's authenticated lifecycle.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time

from .installer_release import (HASH, IDENTIFIER, VERSION, InstallerReleaseError,
    canonical_json, release_id, validate_release, verify_release, verify_signed)

CHANNELS = {'test', 'stable', 'private'}
MAX_RELEASES = 200
MAX_COMPONENTS = 64
MAX_SEARCH = 10000


def catalog_id(catalog):
    return hashlib.sha256(canonical_json({k: v for k, v in catalog.items() if k != 'catalog_id'})).hexdigest()


def version_key(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise InstallerReleaseError('元件版本不正確。')
    main, separator, suffix = version.partition('-')
    parts = []
    if separator:
        for item in suffix.split('.'):
            if item.isdigit():
                if len(item) > 1 and item.startswith('0'):
                    raise InstallerReleaseError('預覽版本的數字識別不能有前置零。')
                parts.append((0, int(item)))
            else:
                parts.append((1, item))
    return (*map(int, main.split('.')), 0 if separator else 1, tuple(parts))


def verify_catalog(envelope, trusted_keys, *, channel, installation_id,
                   previous_sequence=0, previous_catalog_id=None, now=None):
    if channel not in CHANNELS or not isinstance(installation_id, str) or not installation_id:
        raise InstallerReleaseError('工作室更新渠道設定不正確。')
    if type(previous_sequence) is not int or previous_sequence < 0:
        raise InstallerReleaseError('渠道接續紀錄不正確。')
    value = verify_signed(envelope, trusted_keys, 'catalog', channel=channel)
    fields = {'contract_version', 'channel', 'audience', 'sequence', 'expires_at', 'releases', 'revoked'}
    if not isinstance(value, dict) or set(value) != fields:
        raise InstallerReleaseError('渠道發行目錄欄位不正確。')
    if type(value['contract_version']) is not int or value['contract_version'] != 1 or value['channel'] != channel:
        raise InstallerReleaseError('渠道發行目錄身份不符。')
    audience = value['audience']
    if (not isinstance(audience, list) or not audience or len(audience) > 1000 or
            any(not isinstance(x, str) or not x or len(x) > 160 or
                any(ord(c) < 33 or ord(c) == 127 for c in x) for x in audience) or
            len(set(audience)) != len(audience) or ('*' in audience and audience != ['*'])):
        raise InstallerReleaseError('渠道適用工作室清單不正確。')
    if channel == 'private' and '*' in audience:
        raise InstallerReleaseError('私人渠道必須限制指定工作室。')
    if '*' not in audience and installation_id not in audience:
        raise InstallerReleaseError('此渠道不適用於目前工作室。')
    if type(value['sequence']) is not int or value['sequence'] < 1:
        raise InstallerReleaseError('渠道序號不正確。')
    current_time = time.time() if now is None else now
    if type(value['expires_at']) is not int or value['expires_at'] <= current_time:
        raise InstallerReleaseError('渠道發行目錄已到期。')
    identifier = catalog_id(value)
    if (value['sequence'] < previous_sequence or
            (value['sequence'] == previous_sequence and identifier != previous_catalog_id)):
        raise InstallerReleaseError('渠道目錄倒退或相同序號內容已改變。')
    revoked = value['revoked']
    if (not isinstance(revoked, list) or len(revoked) > 1000 or
            any(not isinstance(x, str) or not HASH.fullmatch(x) for x in revoked) or
            len(set(revoked)) != len(revoked)):
        raise InstallerReleaseError('撤回發行清單不正確。')
    releases = value['releases']
    if not isinstance(releases, list) or len(releases) > MAX_RELEASES:
        raise InstallerReleaseError('渠道發行數量超過限制。')
    seen = set()
    identities = {}
    for release in releases:
        manifest = verify_release(release, trusted_keys)
        identifier_release = release_id(manifest)
        if identifier_release in seen:
            raise InstallerReleaseError('渠道發行重複。')
        seen.add(identifier_release)
        key = (manifest['component_id'], manifest['version'])
        if set(manifest['platforms']) & identities.get(key, set()):
            raise InstallerReleaseError('同元件版本有互相衝突的發行。')
        identities.setdefault(key, set()).update(manifest['platforms'])
        if channel == 'stable' and (manifest.get('candidate') or '-' in manifest['version']):
            raise InstallerReleaseError('正式渠道不接受開發候選或預覽版本。')
    value['catalog_id'] = catalog_id(value)
    return value


def _component_set(value):
    if not isinstance(value, (list, tuple, set, frozenset, dict)):
        raise InstallerReleaseError('元件授權清單不正確。')
    result = set(value)
    if len(result) > MAX_COMPONENTS or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in result):
        raise InstallerReleaseError('元件授權清單不正確。')
    return result


def _order(choices):
    result, visiting, visited = [], set(), set()
    def visit(component):
        if component in visiting:
            raise InstallerReleaseError('元件依賴形成循環。')
        if component in visited:
            return
        visiting.add(component)
        for dependency in sorted(choices[component]['manifest'].get('requires', {})):
            if dependency not in choices:
                raise InstallerReleaseError('元件依賴尚未安裝。')
            visit(dependency)
        visiting.remove(component)
        visited.add(component)
        result.append(component)
    for component in sorted(choices):
        visit(component)
    return result


def resolve_updates(releases, installed, allowed_components, requested=None, *, revoked=None, platform=None):
    """Return a dependency-ordered plan, preserving all installed components.

    `releases` must come from verify_catalog (or individually verify_release).
    This pure planner does not confer signature or deployment authority. Each
    adapter must reverify before preparation and immediately before application.
    With requested=None, offer compatible updates to installed components only.
    Explicit component IDs can request a licensed new module; dependencies may
    add only required, licensed components. Existing unrelated modules stay put.
    """
    if isinstance(releases, dict):
        revoked = releases.get('revoked', [])
        releases = releases.get('releases')
    if not isinstance(releases, list) or len(releases) > MAX_RELEASES or not isinstance(installed, dict):
        raise InstallerReleaseError('更新計劃輸入不正確。')
    allowed = _component_set(allowed_components)
    existing = _component_set(installed)
    wanted = existing if requested is None else _component_set(requested)
    if not wanted <= allowed:
        raise InstallerReleaseError('這項更新沒有工作室授權。')
    revoked = set(revoked or [])
    if any(not isinstance(x, str) or not HASH.fullmatch(x) for x in revoked):
        raise InstallerReleaseError('撤回發行清單不正確。')
    platform = platform or sys.platform
    old = {}
    for component, value in installed.items():
        version = value.get('version') if isinstance(value, dict) else value
        version_key(version)
        manifest = value.get('manifest', {}) if isinstance(value, dict) else {}
        if manifest and (manifest.get('component_id') != component or manifest.get('version') != version):
            raise InstallerReleaseError('已安裝元件的版本紀錄不符。')
        old[component] = {'manifest': {'component_id': component, 'version': version,
            'requires': manifest.get('requires', {})}, 'envelope': None, 'release_id': value.get('release_id') if isinstance(value, dict) else None}
    choices = {}
    for envelope in releases:
        if not isinstance(envelope, dict) or 'manifest' not in envelope:
            raise InstallerReleaseError('發行描述不正確。')
        manifest = validate_release(envelope['manifest'])
        component = manifest['component_id']
        identifier = release_id(manifest)
        if component not in allowed or platform not in manifest['platforms'] or manifest.get('revoked') or identifier in revoked:
            continue
        if component in old and version_key(manifest['version']) <= version_key(old[component]['manifest']['version']):
            continue
        record = {'manifest': manifest, 'envelope': envelope, 'release_id': identifier}
        choices.setdefault(component, []).append(record)
    for component in choices:
        choices[component].sort(key=lambda r: version_key(r['manifest']['version']), reverse=True)
    had_update = any(choices.get(component) for component in wanted)
    attempts = 0

    def search(selected, needed, constraints):
        nonlocal attempts
        attempts += 1
        if attempts > MAX_SEARCH or len(needed) > MAX_COMPONENTS:
            raise InstallerReleaseError('元件組合過多，請縮小更新範圍。')
        for component, values in constraints.items():
            if component in selected and selected[component]['manifest']['version'] not in values:
                return None
        pending = needed - selected.keys()
        if not pending:
            try:
                return selected, _order(selected)
            except InstallerReleaseError:
                return None
        component = min(pending, key=lambda c: (c not in wanted, len(choices.get(c, [])), c))
        candidates = list(choices.get(component, []))
        if component in old:
            if component in wanted:
                candidates.append(old[component])
            else:
                candidates.insert(0, old[component])
        if component not in allowed and component not in old:
            return None
        for record in candidates:
            if component in constraints and record['manifest']['version'] not in constraints[component]:
                continue
            requirements = record['manifest'].get('requires', {})
            new_constraints = {k: set(v) for k, v in constraints.items()}
            valid = True
            for dependency, versions in requirements.items():
                if dependency not in allowed and dependency not in old:
                    valid = False
                    break
                new_constraints[dependency] = set(versions) & new_constraints.get(dependency, set(versions))
                if not new_constraints[dependency]:
                    valid = False
                    break
            if valid:
                result = search({**selected, component: record}, needed | set(requirements), new_constraints)
                if result is not None:
                    return result
        return None

    result = search({}, existing | wanted, {})
    if result is None:
        raise InstallerReleaseError('沒有符合所有已安裝元件的版本依賴組合。')
    selected, order = result
    planned = [selected[c]['envelope'] for c in order if selected[c]['envelope'] is not None]
    if had_update and not planned:
        raise InstallerReleaseError('可用更新與目前版本組合不相容。')
    final_versions = {c: selected[c]['manifest']['version'] for c in sorted(selected)}
    intent = {'from': {c: old[c]['manifest']['version'] for c in sorted(old)},
              'releases': [release_id(e['manifest']) for e in planned], 'final_versions': final_versions}
    return {'releases': planned, 'plan_digest': hashlib.sha256(canonical_json(intent)).hexdigest(),
            'final_versions': final_versions}
