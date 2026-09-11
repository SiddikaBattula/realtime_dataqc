"""
Settings for the realtime agent.

Everything that changes per machine comes from realtime_dataqc/.env; the rule
files come from realtime_dataqc/data/. Both are looked up relative to this
folder, so the agent runs the same whether it is started as a script or as a
PyInstaller .exe.
"""

from pathlib import Path
from dotenv import load_dotenv
import sys
import os


def _app_dir():
    """
    The folder the agent runs out of.

    Frozen by PyInstaller the modules are unpacked into a temp directory, so
    data/ and .env are read from next to the .exe instead - that is what lets
    thresholds and credentials be edited on the server without a rebuild.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


APP_DIR = _app_dir()

load_dotenv(APP_DIR / ".env")


class Config:

    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))

    DB_USERNAME = os.getenv("DB_USERNAME")
    DB_PASSWORD = os.getenv("DB_PASSWORD")

    DB_NAME = os.getenv("DB_NAME")
    TABLE_NAME = os.getenv("TABLE_NAME")

    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1"))

    DRILLING_CRITERIA = float(
        os.getenv("DRILLING_CRITERIA", "0.1")
    )

    BASE_DIR = APP_DIR

    DATA_DIR = APP_DIR / "data"
    LOG_DIR = APP_DIR / "logs"

    COLUMN_MAP_FILE = DATA_DIR / "column_mapping.json"
    RANGES_FILE = DATA_DIR / "ranges.json"
    ACTIVITY_FILE = DATA_DIR / "activity.json"
    CONDITIONS_FILE = DATA_DIR / "conditions.json"

    LOG_RETENTION_HOURS = int(
        os.getenv("LOG_RETENTION_HOURS", "24")
    )

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # The rule-file API (config_api.py), which is a separate process from the
    # agent - they share the data/ folder and nothing else.
    CONFIG_API_HOST = os.getenv("CONFIG_API_HOST", "0.0.0.0")
    CONFIG_API_PORT = int(os.getenv("CONFIG_API_PORT", "8000"))

    ALERT_TABLE = os.getenv(
        "ALERT_TABLE",
        "dataqcalert"
    )
