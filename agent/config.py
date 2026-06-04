import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path('/data/config.yml')


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning('config.yml not found at %s — using empty config', path)
        return {}
    except yaml.YAMLError as exc:
        logger.error('Failed to parse config.yml: %s', exc)
        return {}
