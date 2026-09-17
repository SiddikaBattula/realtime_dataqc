"""
Central logging setup.

Two files under LOG_DIR, plus the console:

    app.log      everything, DEBUG and up - the whole process
    alerts.log   only the qc.alerts tree - what the wells have raised,
                 with the values and the reason behind each one

Both rotate hourly and keep LOG_RETENTION_HOURS of history.
purge_old_logs() sweeps leftovers (e.g. after a long shutdown).

Every agent runs in the same process and writes to the same files, so the
well's name is part of the logger name rather than part of each message:

    qc.alerts.KJ-16       that well's alerts
    validation.KJ-16      that well's checks
    well_agent            the loops, each line naming its well

which makes `grep KJ-16 logs/alerts.log` one well's whole story.
"""

import logging
import time
from logging.handlers import TimedRotatingFileHandler

from config import Config

FMT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
DATEFMT = "%d-%m-%Y %H:%M:%S"

_CONFIGURED = False


def _file_handler(filename, level):
    handler = TimedRotatingFileHandler(
        filename=str(Config.LOG_DIR / filename),
        when=Config.LOG_ROTATE_WHEN,               # "H" -> roll over hourly
        interval=Config.LOG_ROTATE_INTERVAL,
        backupCount=Config.LOG_RETENTION_HOURS,    # keep 24 -> 24h of history
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d_%H"
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FMT, DATEFMT))
    return handler


def purge_old_logs(max_age_hours=None):
    """Delete any log file older than the retention window. Returns count deleted."""
    max_age_hours = max_age_hours or Config.LOG_RETENTION_HOURS
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0

    if not Config.LOG_DIR.exists():
        return 0

    for path in Config.LOG_DIR.glob("*.log*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass

    return removed


def setup_logging(level=None):
    """
    Console + rotating file handlers, once per process.

    `level` sets what reaches the console; LOG_LEVEL in .env is the default.
    The file handler always takes DEBUG, so the full detail is on disk even
    when the console is quiet.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if level is None:
        level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)

    Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    purge_old_logs()

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(FMT, DATEFMT))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(_file_handler("app.log", logging.DEBUG))

    # Alerts get their own file as well, so what the wells raised can be read
    # without the connection chatter around it. It keeps propagating to root,
    # so app.log still holds everything in one timeline.
    alerts = logging.getLogger("qc.alerts")
    alerts.setLevel(logging.INFO)
    alerts.addHandler(_file_handler("alerts.log", logging.INFO))

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging ready | dir=%s | retention=%dh",
        Config.LOG_DIR.resolve(), Config.LOG_RETENTION_HOURS,
    )


def get_logger(name):
    return logging.getLogger(name)