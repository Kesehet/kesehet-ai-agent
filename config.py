import json

CONFIG_FILE = "config.json"

def get_config(key: str, default: str = None) -> str:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    return config.get(key, default)