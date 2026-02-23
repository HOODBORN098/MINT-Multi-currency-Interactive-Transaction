"""
ChainPay Client Configuration
==============================
Central config file for the desktop client.
When distributing the app, update API_BASE_URL to point to your server.

For LAN distribution:     API_BASE_URL = "http://192.168.1.100:8443"
For internet distribution: API_BASE_URL = "https://your-ngrok-url.ngrok-free.app"
For local testing only:    API_BASE_URL = "http://127.0.0.1:8443"
"""

import os
import json

# ── Default configuration ──────────────────────────────────────────────────────
# CHANGE THIS before packaging/distributing the app.
DEFAULT_API_BASE_URL = "http://127.0.0.1:8443"

# Set to True when using a valid TLS cert (e.g., ngrok, Let's Encrypt).
# Set to False for self-signed certs or plain HTTP in development.
DEFAULT_VERIFY_SSL = False

# Config file path — sits next to the executable after packaging
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chainpay_config.json")


def load_config() -> dict:
    """Load config from JSON file if it exists, else use defaults."""
    defaults = {
        "api_base_url": DEFAULT_API_BASE_URL,
        "verify_ssl":   DEFAULT_VERIFY_SSL,
        "app_name":     "ChainPay",
        "app_version":  "2.0.0",
    }
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r") as f:
                loaded = json.load(f)
                return {**defaults, **loaded}
        except Exception:
            pass
    return defaults


def save_config(cfg: dict):
    """Persist config changes to disk."""
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# Global config object — imported everywhere
CONFIG = load_config()


def get_api_url(path: str) -> str:
    """Build a full API URL from a path fragment."""
    base = CONFIG["api_base_url"].rstrip("/")
    path = path.lstrip("/")
    return f"{base}/{path}"