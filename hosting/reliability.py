"""CP02 release guards. Later capabilities cannot be enabled before implementation."""
from dataclasses import dataclass
import os

INPUT_PROTOCOL_VERSION = 1  # legacy ads snapshot; ad_context belongs to a later release
EXECUTOR_PROTOCOL_VERSION = 1
FLAGS = ('AD_CONTEXT_V1_ENABLED', 'SHARED_ASSET_WRITES_ENABLED',
         'NARROW_TRANSACTIONS_ENABLED', 'SSE_SHARED_READER_ENABLED',
         'DYNAMIC_EXECUTOR_ENABLED')

@dataclass(frozen=True)
class ReliabilityConfig:
    deployment_mode: str
    input_protocol_version: int
    executor_protocol_version: int
    features: dict

def load_reliability(env=None):
    env = os.environ if env is None else env
    mode = env.get('BACKEND_DEPLOYMENT_MODE', 'development')
    if mode not in ('development', 'multi_node'):
        raise ValueError('invalid_BACKEND_DEPLOYMENT_MODE')
    versions = []
    for name in ('HOSTING_INPUT_PROTOCOL_VERSION', 'HOSTING_EXECUTOR_PROTOCOL_VERSION'):
        if env.get(name, '1') != '1':
            raise ValueError('unsupported_' + name)
        versions.append(1)
    flags = {}
    for name in FLAGS:
        value = env.get(name, 'false').lower()
        if value not in ('true', 'false', '1', '0'):
            raise ValueError('invalid_' + name)
        flags[name] = value in ('true', '1')
        if flags[name] and name != 'AD_CONTEXT_V1_ENABLED':
            raise ValueError(name + '_not_implemented_yet')
    # CP08/CP19/CP22 must remove this guard only with shared storage/ownership/deployment.
    if mode == 'multi_node':
        raise ValueError('multi_node_not_implemented_yet')
    return ReliabilityConfig(mode, *versions, flags)
