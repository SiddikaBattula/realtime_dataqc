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

    # Credentials are shared across wells; the host and database name are not -
    # each well supplies its own through POST /wells.
    DB_PORT = int(os.getenv("DB_PORT", "3306"))

    DB_USERNAME = os.getenv("DB_USERNAME")
    DB_PASSWORD = os.getenv("DB_PASSWORD")

    # The single-row table each well exposes its latest reading in.
    TABLE_NAME = os.getenv("TABLE_NAME", "timebaselastrecord")

    # Seconds between readings.
    CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", "1"))

    # Seconds to wait before reconnecting after the loop hits an error.
    RETRY_INTERVAL = float(os.getenv("RETRY_INTERVAL", "5"))

    # Hole depth minus bit depth, in metres: at or below this the bit is on
    # bottom (DRILLING), above it it is not (NON DRILLING).
    DRILLING_CRITERIA = float(os.getenv("DRILLING_CRITERIA", "0.1"))

    # ---- where things live -------------------------------------------
    BASE_DIR = APP_DIR

    DATA_DIR = APP_DIR / "data"
    LOG_DIR = APP_DIR / "logs"
    OUTPUT_DIR = APP_DIR / "output"

    COLUMN_MAP_FILE = DATA_DIR / "column_mapping.json"
    RANGES_FILE = DATA_DIR / "ranges.json"
    ACTIVITY_FILE = DATA_DIR / "activity.json"
    CONDITIONS_FILE = DATA_DIR / "conditions.json"
    DISPLAY_NAME_FILE = DATA_DIR / "display_name.json"


    # ---- logging -------------------------------------------------------
    LOG_RETENTION_HOURS = int(os.getenv("LOG_RETENTION_HOURS", "24"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # ---- alert storage -------------------------------------------------
    # Alerts are written to output/<database_name>/alerts.json and nowhere
    # else - nothing is inserted into any database.

    # Hours an alert is kept in that file. Older ones are removed by the
    # well's agent (see WellAgent._prune_if_due).
    ALERT_RETENTION_HOURS = float(os.getenv("ALERT_RETENTION_HOURS", "24"))

    # ---- the rule-file API (config_api.py) -----------------------------
    CONFIG_API_HOST = os.getenv("CONFIG_API_HOST", "0.0.0.0")
    CONFIG_API_PORT = int(os.getenv("CONFIG_API_PORT", "8000"))
