"""
Settings for the realtime agent.

Everything that changes per machine comes from realtime_dataqc/.env; the rule
files come from realtime_dataqc/data/. Both are looked up relative to this
folder, so the agent runs the same whether it is started as a script or as a
PyInstaller .exe.
"""

from pathlib import Path
from dotenv import load_dotenv
import json
import logging
import os
import re
import sys
import threading


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
    DEFAULT_DRILLING_CRITERIA = float(os.getenv("DEFAULT_DRILLING_CRITERIA"))
    DEFAULT_BD_THRESHOLD_DRILLING = float(os.getenv("DEFAULT_BD_THRESHOLD_DRILLING"))
    DEFAULT_BD_THRESHOLD_NON_DRILLING=float(os.getenv("DEFAULT_BD_THRESHOLD_NON_DRILLING"))
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

    # ---- the ten-minute alert digest (mailer/) -------------------------
    # Off unless EMAIL_ENABLED says otherwise, so a machine with no mail
    # server runs exactly as it did before any of this existed.
    EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }

    # Seconds between passes, and so the length of an ordinary window. A pass
    # that is late or missed makes the next window longer rather than leaving
    # a gap - each region's window runs from its own last successful send.
    EMAIL_INTERVAL_SECONDS = float(os.getenv("EMAIL_INTERVAL_SECONDS", "600"))

    # ---- the mail server -----------------------------------------------
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

    # SSL wraps the socket from the first byte (port 465); TLS connects in the
    # clear and upgrades with STARTTLS (port 587). Set rather than guessed
    # from the port: the wrong one gives a connection that hangs until it
    # times out instead of failing outright.
    SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }

    # Left blank for a relay that does not ask who you are.
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

    SMTP_TIMEOUT = float(os.getenv("SMTP_TIMEOUT", "30"))

    # Who it comes from. SMTP_FROM must be an address the server above accepts
    # as a sender, which for Microsoft 365 means the mailbox being logged into.
    SMTP_FROM = os.getenv("SMTP_FROM", "") or os.getenv("EMAIL_FROM", "")
    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Real-Time Data QC")

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


# ===========================================================================
# Who is emailed about each base region - data/email_config.json
#
# Here rather than in a module of its own, so there is one place to look for
# anything configurable. The class above is the settings that come out of
# .env and never change while the app runs; this is the one list that is
# edited from the dashboard while it does.
#
#     {
#       "mumbai": {
#         "base_head":        "someone@ofiindia.com",
#         "operational_head": "someone.else@ofiindia.com"
#       },
#       "pune": { ... }
#     }
#
# The region is the key, and under it is whoever should be told about that
# base - as many people as the base has, under whatever names suit. The roles
# are not fixed: adding "drilling_engineer" to a region sends to them too,
# with nothing here to change.
#
# A well says which region it is on beside its IP address, and that name is
# looked up here. Two separate things on purpose: a coordinator changing does
# not mean re-saving every well on that base, and a well moving base does not
# mean editing this file.
#
# logging directly rather than logger.get_logger: logger.py imports this
# module, so importing it back would be a circular import.
# ===========================================================================

EMAIL_CONFIG_FILE = Config.DATA_DIR / "email_config.json"

# Deliberately loose. The real check is whether the mail server accepts the
# address; this only catches a blank box or a name typed where an address goes.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The API writes this file while the digest thread reads it.
_EMAIL_LOCK = threading.RLock()

_email_log = logging.getLogger("email-config")


class EmailConfigError(ValueError):
    """Something a person can fix, with a sentence saying what."""


def _clean_people(region, people):
    """One region's {role: address}, checked and tidied."""
    if not isinstance(people, dict):
        raise EmailConfigError(
            f"{region!r} must be a set of people, like "
            '{"base_head": "someone@example.com"}'
        )

    cleaned = {}

    for role, address in people.items():
        name = str(role or "").strip()
        email = str(address or "").strip()

        if not name:
            raise EmailConfigError(f"{region}: a person needs a role name")

        # A role left blank is someone not appointed yet, which is worth
        # keeping in the file as a reminder - it is simply not written to.
        if email and not _EMAIL_SHAPE.match(email):
            raise EmailConfigError(
                f"{region} / {name}: {email!r} is not an email address"
            )

        cleaned[name] = email

    if not any(cleaned.values()):
        raise EmailConfigError(
            f"{region} has nobody to email - give at least one role an address"
        )

    return cleaned


def validate_email_config(document):
    """The list as it will be stored, or an EmailConfigError."""
    if not isinstance(document, dict):
        raise EmailConfigError(
            'email_config.json must be regions, like {"mumbai": {...}}'
        )

    cleaned = {}
    seen = {}

    for region, people in document.items():
        name = str(region or "").strip()

        if not name:
            raise EmailConfigError("A region needs a name")

        # Casefolded, because the region is typed twice - once on the well and
        # once here - and "Mumbai" against "mumbai" is one base, not two.
        key = name.casefold()

        if key in seen:
            raise EmailConfigError(
                f"{name} is listed twice - once as {seen[key]!r} "
                f"and again as {name!r}"
            )

        seen[key] = name
        cleaned[name] = _clean_people(name, people)

    return cleaned


def load_email_config():
    """
    {region: {role: address}} as it stands, or {} when there is no file yet.

    A missing file means no regions have been entered - not an error - so the
    agent sends nothing until the dashboard writes one. A file that cannot be
    read is reported and treated as empty: a broken list should stop the
    emails, not the monitoring.
    """
    with _EMAIL_LOCK:
        if not EMAIL_CONFIG_FILE.exists():
            return {}

        try:
            with open(EMAIL_CONFIG_FILE, "r", encoding="utf-8") as fh:
                document = json.load(fh)

        except (OSError, ValueError) as exc:
            _email_log.error(
                "%s could not be read (%s) - no emails will be sent",
                EMAIL_CONFIG_FILE.name, exc,
            )
            return {}

    try:
        return validate_email_config(document)

    except EmailConfigError as exc:
        _email_log.error("%s is not usable: %s", EMAIL_CONFIG_FILE.name, exc)
        return {}


def save_email_config(document):
    """Check the regions, then write them. Returns them as stored."""
    cleaned = validate_email_config(document)

    with _EMAIL_LOCK:
        EMAIL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Written beside the real file and moved into place: the digest thread
        # may read this at any moment and must never catch it half-written.
        temporary = EMAIL_CONFIG_FILE.with_suffix(".json.writing")

        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(cleaned, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(temporary, EMAIL_CONFIG_FILE)

    _email_log.info("Saved %d email region(s)", len(cleaned))

    return cleaned


def email_regions():
    """
    region (casefolded) -> {"region": as typed, "people": {role: address}}.

    Casefolded so a well on "Mumbai" finds the region saved as "mumbai".
    """
    return {
        region.casefold(): {"region": region, "people": people}
        for region, people in load_email_config().items()
    }


def email_addresses(people):
    """
    The addresses one region sends to, in the order its roles are listed.

    Deduplicated, because one person often holds two roles on a small base -
    base head and operational head both being the same mailbox is normal, and
    it should be addressed once rather than sent the same email twice.
    """
    seen = set()
    addresses = []

    for address in people.values():
        key = address.casefold()

        if address and key not in seen:
            seen.add(key)
            addresses.append(address)

    return addresses
