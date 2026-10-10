"""Marketing rollout configuration. Later stages cannot be enabled prematurely."""
import os

FLAGS = ('MARKETING_EVENTS_ENABLED', 'MARKETING_CONTENT_ENABLED', 'MARKETING_AGENT_ACTIONS_ENABLED', 'MARKETING_DELIVERY_ENABLED', 'MARKETING_LEARNING_ENABLED')

def load_marketing(env=None):
    env = os.environ if env is None else env
    features = {}
    for index, key in enumerate(FLAGS):
        raw = env.get(key, 'false')
        if raw not in ('true', 'false', '1', '0'):
            raise ValueError('invalid_' + key)
        features[key] = raw in ('true', '1')
        if features[key] and index and not features[FLAGS[index - 1]]:
            raise ValueError('missing_dependency_' + key)
        if features[key] and index:
            raise ValueError(key + '_not_implemented_yet')
    environment = env.get('MARKETING_ENVIRONMENT', 'synthetic')
    if environment not in ('synthetic', 'sandbox', 'production'):
        raise ValueError('invalid_MARKETING_ENVIRONMENT')
    if env.get('MARKETING_PROTOCOL_VERSION', '1') != '1':
        raise ValueError('unsupported_MARKETING_PROTOCOL_VERSION')
    return {'features': features, 'environment': environment, 'protocolVersion': 1}
