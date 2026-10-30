"""Coordinate licensed component updates without replacing other studios or services."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time
from blueprint_folders.path_safety import has_link

from .installer import (CoreSupervisor, _config, _lock, _private, _program_root, _public, _read,
                        _save, _state, _owner_path, FORMAT, InstallerBusyError,
                        read_feed, read_status, create_checkpoint, ensure_quiescent, _same_schema)
from .maintenance import atomic_json, DataLease
from .installer_modules import ModuleRuntime, private_path, module_checkpoint
from .installer_release import prepare_release, release_id
from .installer_catalog import verify_catalog, resolve_updates


def installed_components(state):
    values = dict(state.get('components', {}))
    values.setdefault('blueprint-core', state['installed'])
    return values


def register_module(root, envelope, node, config_file):
    """Local owner setup: adopt an explicit existing Agent ID, never create a card."""
    if has_link(node):
        raise ValueError('模組執行環境不能包含符號連結或重新解析點。')
    config = _config(root)
    root = _program_root(root)
    private = _read(private_path(config_file))
    component = envelope.get('manifest', {}).get('component_id')
    if component != 'bluesai-cs-0001' or private.get('component_id') != component:
        raise ValueError('目前模組接駁只支援指定的客服元件。')
    if (private.get('host') != '127.0.0.1' or type(private.get('port')) is not int or
            not 1 <= private['port'] <= 65535 or
            not isinstance(private.get('expected_agent_id'), str) or not private['expected_agent_id']):
        raise ValueError('請指定原 Agent ID 及獨立 loopback 服務。')
    data = private_path(private['data_dir'])
    for other in [root, *(_private(config[key]) for key in ('data_dir', 'backup_dir', 'recovery_dir')),
                  *(private_path(value['data_dir']) for value in config.get('modules', {}).values())]:
        if data == other or data in other.parents or other in data.parents:
            raise ValueError('模組資料須與其他工作室及資料域分開。')
    ports = [config['port'], *(value['port'] for value in config.get('modules', {}).values())]
    if private['port'] in ports:
        raise ValueError('模組服務埠已被此工作室使用。')
    claim = _owner_path(data)
    if claim.exists():
        raise ValueError('模組資料已被其他受管實例綁定。')
    with _lock(root, '.supervisor.lock', wait=False), _lock(root, '.state.lock'):
        state = _state(root)
        if state['phase'] in ('applying', 'apply_requested', 'recovery_required'):
            raise ValueError('請先處理未完成的更新。')
        if component in config.get('modules', {}):
            raise ValueError('此模組已安裝，請使用原更新操作。')
        prepared = prepare_release(envelope, config['trusted_keys'], root / 'releases',
            installed=installed_components(state), runtimes={'node': str(Path(node).resolve())})
        prepared['envelope'] = envelope
        directory = root / 'module-configs'
        directory.mkdir(exist_ok=True, mode=0o700)
        saved = directory / (component + '.json')
        with saved.open('x', encoding='utf-8') as stream:
            saved.chmod(0o600)
            json.dump(private, stream, ensure_ascii=False)
        data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with claim.open('x', encoding='utf-8') as stream:
            claim.chmod(0o600)
            json.dump({'format': FORMAT, 'root': str(root), 'installation_id': config['installation_id'],
                       'component_id': component}, stream)
        config.setdefault('modules', {})[component] = {'node': str(Path(node).resolve()),
            'data_dir': str(data), 'config_file': str(saved), 'port': private['port'],
            'expected_agent_id': private['expected_agent_id']}
        atomic_json(root / 'installation.json', config)
        components = installed_components(state)
        components[component] = prepared
        _save(root, state, components=components)
    return read_status(root)


def configure_channel(root, feed, channel):
    if channel not in {'test', 'stable', 'private'}:
        raise ValueError('更新渠道不正確。')
    config = _config(root)
    read_feed(feed)  # Bound by the owner, never supplied by the update browser.
    with _lock(root, '.supervisor.lock', wait=False):
        config.update(feed=str(feed), channel=channel)
        atomic_json(Path(root) / 'installation.json', config)
    return read_status(root)


class ComponentSupervisor(CoreSupervisor):
    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        self.modules = {name: ModuleRuntime(self, name) for name in self.config.get('modules', {})}

    def _catalog(self):
        state = self._get()
        document = read_feed(self.config['feed'])
        channel = self.config.get('channel')
        if not channel:
            if self.modules:
                raise ValueError('多元件更新需要已綁定的簽章發行渠道。')
            return {'releases': document.get('releases', [document]), 'revoked': []}
        history = state.get('catalogs', {}).get(channel, {})
        catalog = verify_catalog(document, self.config['trusted_keys'], channel=channel,
            installation_id=self.config['installation_id'], previous_sequence=history.get('sequence', 0),
            previous_catalog_id=history.get('catalog_id'))
        catalogs = dict(state.get('catalogs', {}))
        catalogs[channel] = {'sequence': catalog['sequence'], 'catalog_id': catalog['catalog_id']}
        self._update(catalogs=catalogs)
        return catalog

    def _agent(self, component, release=None, operation_id=None, *, action=None, check_busy=True):
        from urllib.request import Request, build_opener, ProxyHandler
        from .installer import _NoRedirect
        child = self._get().get('child')
        if not child:
            raise ValueError('核心服務尚未確認，未改動 Agent 登記。')
        module = self.config['modules'][component]
        body = {'action': 'agent-status', 'agent_id': module['expected_agent_id']}
        if action is not None:
            body['action'] = action
        if release:
            manifest = release['manifest']
            descriptor = json.loads((Path(release['path']) / 'managed-component.json').read_text(encoding='utf-8'))
            agent = json.loads((Path(release['path']) / descriptor['agent_manifest']).read_text(encoding='utf-8'))
            body.update(action='agent-upgrade', manifest=agent,
                source={'repository': manifest['source_repository'], 'revision': manifest['source_revision']},
                upgrade_key=operation_id + '-' + component)
        request = Request(f'http://127.0.0.1:{self.config["port"]}/api/installer/internal',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'X-Blueprint-Installer': child['token']})
        with build_opener(ProxyHandler({}), _NoRedirect).open(request, timeout=20) as response:
            result = json.loads(response.read(256 * 1024))
        if result['agent']['id'] != module['expected_agent_id'] or result['agent']['manifest']['id'] != component:
            raise ValueError('原 Agent 身份不符，未建立替代卡片。')
        if check_busy and result.get('busy'):
            raise InstallerBusyError('原 Agent 還有未完成工作，請先完成或取消原工作。')
        if release and (result['agent']['source'] != body['source'] or result['agent']['manifest']['version'] != release['version']):
            raise ValueError('同 ID 套件更新尚未讀回。')
        return result

    def check(self):
        if not self.modules and not self.config.get('channel'):
            return super().check()
        state = self._get()
        if state['phase'] in ('applying', 'apply_requested', 'recovery_required'):
            return _public(state)
        self._update(phase='preparing', error=None)
        try:
            self._verify_installed()
            catalog = self._catalog()
            components = installed_components(state)
            plan = resolve_updates(catalog, components, allowed_components=set(components))
            prepared = []
            for envelope in plan['releases']:
                component = envelope['manifest']['component_id']
                runtime = {'node': self.config['modules'][component]['node']} if component in self.modules else None
                item = prepare_release(envelope, self.config['trusted_keys'], self.root / 'releases',
                    installed=plan['final_versions'], runtimes=runtime)
                item['envelope'] = envelope
                if item['manifest']['database_schema'] != components[component]['manifest']['database_schema']:
                    raise ValueError('此元件需要資料遷移，未提供自動套用。')
                prepared.append(item)
            if not prepared:
                self._update(phase='installed', prepared=None, prepared_plan=None, public_plan=[],
                             check_requested=False, last_check=time.time())
            else:
                public = [{'component_id': r['component_id'], 'from': components[r['component_id']]['version'], 'to': r['version']} for r in prepared]
                alias = copy.deepcopy(prepared[0])
                alias.update(release_id=plan['plan_digest'], component_id=prepared[0]['component_id'] if len(prepared) == 1 else 'update-plan')
                alias['manifest'] = {'notes': '\n'.join(r['component_id'] + ': ' + str(r['manifest'].get('notes', '')) for r in prepared)}
                self._update(phase='ready', prepared=alias, prepared_plan={'releases': prepared, 'plan_digest': plan['plan_digest']},
                             public_plan=public, check_requested=False, last_check=time.time())
        except InstallerBusyError as exc:
            self._update(phase='blocked', error=str(exc), prepared=None, check_requested=False, last_check=time.time())
        except (ValueError, OSError, KeyError, TypeError):
            self._update(phase='prepare_failed', error='發行渠道、相依版本或套件準備未通過；現有服務保持使用。',
                         prepared=None, prepared_plan=None, check_requested=False, last_check=time.time())
        return read_status(self.root)

    def apply(self):
        state = self._get()
        if not state.get('prepared_plan'):
            return super().apply()
        if state['phase'] != 'apply_requested':
            raise ValueError('沒有已確認的元件更新。')
        plan, current = state['prepared_plan'], installed_components(state)
        try:
            self._verify_installed()
            catalog = self._catalog()  # Revalidate before any effect on a running service.
            listed = {release_id(e['manifest']) for e in catalog['releases']}
            for item in plan['releases']:
                if item['release_id'] not in listed or item['release_id'] in catalog.get('revoked', []):
                    raise ValueError('候選已撤回。')
                component = item['component_id']
                prepare_release(item['envelope'], self.config['trusted_keys'], self.root / 'releases',
                    installed={**current, **{i['component_id']: i for i in plan['releases']}},
                    runtimes={'node': self.config['modules'][component]['node']} if component in self.modules else None)
        except (OSError, ValueError, KeyError, TypeError):
            self._update(phase='prepare_failed', prepared=None, prepared_plan=None,
                         error='套用前的渠道或產物核對未通過；現有服務保持使用。')
            return read_status(self.root)
        core_update = any(i['component_id'] == 'blueprint-core' for i in plan['releases'])
        participants = list(self.modules) if core_update else [i['component_id'] for i in plan['releases']]
        quiesced = []
        effects = False
        try:
            for component in participants:
                registry = self._agent(component, check_busy=False)
                originals = dict(self._get().get('original_agents', {}))
                originals[component] = registry['agent']
                self._update(original_agents=originals)
                quiesced.append(component)
                self._agent(component, action='agent-quiesce')
                self.modules[component].quiesce()
            if core_update:
                ensure_quiescent(self.config)
            self._update(phase='applying', pending_plan=plan, completed_components=[], error=None)
            for item in plan['releases']:
                component = item['component_id']
                if component == 'blueprint-core':
                    _same_schema(self.config, current[component], item)
                    effects = True
                    self._stop_child()
                    with DataLease(self.config['data_dir']):
                        ensure_quiescent(self.config)
                        protected = create_checkpoint(self.config, state['request']['operation_id'])
                    self._update(checkpoint=protected)
                    self._launch(item)
                else:
                    effects = True
                    self.modules[component].stop()
                    protected = module_checkpoint(self.config, component, state['request']['operation_id'])
                    checkpoints = dict(self._get().get('module_checkpoints', {})); checkpoints[component] = protected
                    self._update(module_checkpoints=checkpoints)
                    self.modules[component].launch(item)  # Starts paused.
                    observed = dict(self._get().get('observed_components', {}))
                    observed[component] = {'version': item['version'], 'release_id': item['release_id'],
                                           'runtime_verified': True, 'registry_verified': False}
                    self._update(observed_components=observed)
                    self._agent(component, item, state['request']['operation_id'])
                    self._agent(component)  # Independent current readback.
                    observed[component]['registry_verified'] = True
                    self._update(observed_components=observed)
                current[component] = item
                completed = self._get().get('completed_components', []) + [component]
                self._update(components=current, installed=current['blueprint-core'], completed_components=completed)
            for component in participants:
                self.modules[component].control('resume')
                self._agent(component, action='agent-resume', check_busy=False)
            history = self._get().get('history', []) + [{'operation_id': state['request']['operation_id'],
                       'plan_digest': plan['plan_digest'], 'completed_at': time.time()}]
            self._update(phase='complete', prepared=None, prepared_plan=None, pending_plan=None,
                         history=history, error=None)
        except InstallerBusyError as exc:
            if effects:
                self._update(phase='recovery_required', error='已有元件切換；請先核對實際版本與原工作，不會重送或回退資料。')
            else:
                try:
                    for component in quiesced:
                        self.modules[component].control('resume')
                        self._agent(component, action='agent-resume', check_busy=False)
                    self._update(phase='blocked', error=str(exc))
                except (OSError, ValueError):
                    self._update(phase='recovery_required', error='延期更新後的客服恢復尚未確認；核心保持運行。')
        except (OSError, ValueError, KeyError, TypeError):
            # Partial component states remain truthful. Do not restore old data,
            # resend events, or restart unverified binaries to manufacture success.
            self._update(phase='recovery_required', error='元件更新結果未能全部確認；資料、檢查點與已完成步驟已保留。')
        return read_status(self.root)

    def run(self):
        if not self.modules:
            return super().run()
        with _lock(self.root, '.supervisor.lock', wait=False):
            state = self._get()
            if state['phase'] in ('applying', 'recovery_required'):
                raise ValueError('上次元件更新結果未知，請先核對原操作，不會盲目重啟。')
            if state.get('child'):
                self._internal(state['child'])
            else:
                self._launch(state['installed'])
            try:
                for component, runtime in self.modules.items():
                    try:
                        # Ordinary restart must leave the core available to finish
                        # existing jobs. Only a real package change needs idle jobs.
                        registry = self._agent(component, action='agent-quiesce', check_busy=False)
                        release = installed_components(self._get())[component]
                        if component in self._get().get('module_children', {}):
                            actual = runtime.control()
                            if (actual.get('version') != release['version'] or
                                    actual.get('database_schema') != release['manifest']['database_schema']):
                                raise ValueError('已運行客服版本與安裝紀錄不符。')
                        else:
                            runtime.launch(release)
                        if registry['agent']['source'] != {'repository': release['manifest']['source_repository'], 'revision': release['manifest']['source_revision']} or registry['agent']['manifest']['version'] != release['version']:
                            self._agent(component, release, 'bootstrap-' + release['release_id'][:16])
                            self._agent(component)
                        runtime.control('resume')
                        self._agent(component, action='agent-resume', check_busy=False)
                    except (OSError, ValueError, KeyError, TypeError):
                        self._update(phase='recovery_required', error='客服啟動或原登記尚未確認；核心保持運行，可處理原工作後再核對。')
                next_check = time.monotonic()
                next_health = time.monotonic()
                while not self.stop_event.wait(.5):
                    state = self._get()
                    if state['phase'] == 'apply_requested':
                        self.apply()
                    elif state.get('check_requested') or time.monotonic() >= next_check:
                        self.check(); next_check = time.monotonic() + self.check_interval
                    if time.monotonic() >= next_health:
                        self._internal(self._get()['child'])
                        for component, runtime in self.modules.items():
                            if component in self._get().get('module_children', {}):
                                try:
                                    runtime.control()
                                except (OSError, ValueError):
                                    self._update(phase='recovery_required', error='客服程序狀態未能確認；核心保持運行，請核對原操作。')
                        self._healthy(); next_health = time.monotonic() + 5
            finally:
                cleanup_errors = []
                for component, runtime in self.modules.items():
                    if component in self._get().get('module_children', {}):
                        try:
                            runtime.stop()
                        except (OSError, ValueError):
                            cleanup_errors.append(component)
                try:
                    self._stop_child()
                except (OSError, ValueError):
                    cleanup_errors.append('blueprint-core')
                if cleanup_errors:
                    self._update(phase='recovery_required', error='部分受管程序尚未確認停止；已保留原身份與操作紀錄。',
                                 cleanup_pending=cleanup_errors)
