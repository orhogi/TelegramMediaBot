from os import getenv

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


def _require_env(name):
    value = getenv(name)
    if not value:
        raise ConfigError(f"{name} is not set in environment/.env")
    return value


class Config:
    load_dotenv()

    try:
        API_ID = int(_require_env("API_ID"))
    except ValueError:
        raise ConfigError("API_ID must be a valid integer")

    API_HASH = _require_env("API_HASH")
    TOKEN = _require_env("TOKEN")

    _raw_devs = _require_env("DEVS").split(",")
    _devs = set()
    for x in _raw_devs:
        x = x.strip()
        if x:
            try:
                _devs.add(int(x))
            except ValueError:
                raise ConfigError(f"DEVS contains invalid user ID: '{x}'")
    DEVS = _devs

    STRING_SESSION = getenv("STRING_SESSION") or None
    FORWARD_CHAT_ID = getenv("FORWARD_CHAT_ID") or None
    MAX_CONCURRENT_DOWNLOADS = max(int(getenv("MAX_CONCURRENT_DOWNLOADS", "1")), 1)
    FLOOD_WAIT_DELAY = max(int(getenv("FLOOD_WAIT_DELAY", "10")), 0)


config = Config()
