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

    # Seconds between the "still here, this is what I am doing" line each agent
    # writes. A quiet log is otherwise unreadable: a well with nothing wrong
    # and a well whose thread has died look exactly the same.
    HEARTBEAT_SECONDS = float(os.getenv("HEARTBEAT_SECONDS", "60"))

    # Seconds the rig may keep answering with the identical row before the
    # heartbeat calls the feed stale. The rig is up and the query works, but
    # the values have stopped moving - which is the failure that used to look
    # like everything being fine.
    STALE_ROW_SECONDS = float(os.getenv("STALE_ROW_SECONDS", "120"))

    # Seconds between the agent manager's passes over the registry, which is
    # how long a well added in the dashboard waits before its agent starts and
    # how quickly a dead agent thread is noticed and restarted.
    AGENT_POLL_INTERVAL = float(os.getenv("AGENT_POLL_INTERVAL", "5"))

    # Seconds between sweeps of a well's alert file for alerts past
    # ALERT_RETENTION_HOURS. Saving a new alert sweeps too; this is what clears
    # them on a well that has gone quiet.
    ALERT_PRUNE_INTERVAL = float(os.getenv("ALERT_PRUNE_INTERVAL", "60"))

    # Metres of hole depth minus bit depth that a new well's form opens with.
    # There is no DRILLING_CRITERIA any more: the margin belongs to the well,
    # not to this machine, and is entered per well in the dashboard. This is
    # only the starting value, and what a well saved before the setting existed
    # is read under.
    DEFAULT_DRILLING_CRITERIA = float(os.getenv("DEFAULT_DRILLING_CRITERIA", "0.1"))
    DEFAULT_BIT_DEPTH_THRESHOLD = float(os.getenv("DEFAULT_BIT_DEPTH_THRESHOLD", "5"))
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

    # When the log files roll over, as TimedRotatingFileHandler spells it:
    # "H" hourly, "D" daily, "M" per minute. LOG_RETENTION_HOURS is the number
    # of rolled files kept, so the two together decide how much history is on
    # disk - 24 hourly files is a day.
    LOG_ROTATE_WHEN = os.getenv("LOG_ROTATE_WHEN", "H")
    LOG_ROTATE_INTERVAL = int(os.getenv("LOG_ROTATE_INTERVAL", "1"))

    # Seconds before a set of alerts that has not changed is written out again.
    # The block itself is only written when what is wrong changes; this is the
    # one line in between that says the problem is still there.
    LOG_REPEAT_SECONDS = float(os.getenv("LOG_REPEAT_SECONDS", "60"))

    # ---- alert storage -------------------------------------------------
    # Alerts are written to output/<database_name>/alerts.json and nowhere
    # else - nothing is inserted into any database.

    # Hours an alert is kept in that file. Older ones are removed by the
    # well's agent (see WellAgent._prune_if_due).
    ALERT_RETENTION_HOURS = float(os.getenv("ALERT_RETENTION_HOURS", "24"))

    # How many alerts GET /alerts/{well} returns when asked for no particular
    # number, and the most it will return however many are asked for.
    ALERT_PAGE_DEFAULT = int(os.getenv("ALERT_PAGE_DEFAULT", "200"))
    ALERT_PAGE_MAX = int(os.getenv("ALERT_PAGE_MAX", "5000"))

    # ---- the rule-file API (config_api.py) -----------------------------
    CONFIG_API_HOST = os.getenv("CONFIG_API_HOST", "0.0.0.0")
    CONFIG_API_PORT = int(os.getenv("CONFIG_API_PORT", "8000"))

    # ---- the dashboard (frontend/) -------------------------------------
    # The page is static and cannot read .env, so it asks GET /settings for
    # these when it loads. That keeps .env the only place any of it is set -
    # change a value here and reload the page, no editing of app.js.
    #
    # Seconds between polls of /wells and /alerts.
    DASHBOARD_POLL_SECONDS = float(os.getenv("DASHBOARD_POLL_SECONDS", "5"))

    # Seconds a newly added well keeps saying "starting" rather than "all
    # clear". The manager takes up to AGENT_POLL_INTERVAL to notice it, so
    # this wants to be comfortably the larger of the two.
    DASHBOARD_STARTING_SECONDS = float(os.getenv("DASHBOARD_STARTING_SECONDS", "8"))

    # Minutes an alert stays on its card, and the most a card will hold.
    DASHBOARD_ALERT_MAX_AGE_MINUTES = float(
        os.getenv("DASHBOARD_ALERT_MAX_AGE_MINUTES", "30")
    )
    DASHBOARD_ALERT_LIMIT = int(os.getenv("DASHBOARD_ALERT_LIMIT", "1000"))

    # How far a card can be dragged: pixels of alert list, and grid columns.
    DASHBOARD_CARD_MIN_HEIGHT = int(os.getenv("DASHBOARD_CARD_MIN_HEIGHT", "90"))
    DASHBOARD_CARD_MAX_HEIGHT = int(os.getenv("DASHBOARD_CARD_MAX_HEIGHT", "900"))
    DASHBOARD_CARD_MAX_COLUMNS = int(os.getenv("DASHBOARD_CARD_MAX_COLUMNS", "4"))

    
