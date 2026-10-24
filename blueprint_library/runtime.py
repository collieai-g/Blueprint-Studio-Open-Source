"""An authenticated, versioned contract for adapters, separate from releases.

Capabilities describe this process, not a completed deployment or off-host backup.
An incompatible API change must introduce another contract rather than silently
changing the meaning of this one.
"""
from blueprint_folders import __version__
from .store import SCHEMA_VERSION


def runtime_contract(*, managed):
    return {
        'product': 'Blueprint Studio Backend',
        'product_version': __version__,
        'api_contract': 'blueprint-native/1',
        'storage': 'native',
        'database_schema': SCHEMA_VERSION,
        'maintenance_available': bool(managed),
        'backup_scope': ['database', 'attachments', 'login'] if managed else [],
        'off_host_backup': 'not_managed',
        'agent_executor': 'not_configured',
        'agent_contract': 'blueprint-agent/1',
        'agent_handoff': 'connected_client',
        'integration_contract': 'blueprint-integration/1.0',
        'integration_authentication': 'scoped_bearer',
        'autonomous_agents': False,
    }
