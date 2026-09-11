"""
Central logging setup.

- Console + rotating files under LOG_DIR.
- Rotates hourly, keeps LOG_RETENTION_HOURS files, so nothing older
  than 24h stays on disk.
- purge_old_logs() sweeps leftovers (e.g. after a long shutdown).
"""

import logging
import time
from logging.handlers import TimedRotatingFileHandler

from config import Config

FMT = "%(asctime)s | %(levelname)-8s | %(name)-26s | %(message)s"
DATEFMT = "%d-%m-%Y %H:%M:%S"

_CONFIGURED = False


def _file_handler(filename, level):
    handler = TimedRotatingFileHandler(
        filename=str(Config.LOG_DIR / filename),
        when="H",                                  # rotate every hour
        interval=1,
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
    # root.addHandler(_file_handler("error.log", logging.ERROR))

    # Alerts also get their own file so you can tail just the QC hits.
    alerts = logging.getLogger("qc.alerts")
    alerts.setLevel(logging.INFO)

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging ready | dir=%s | retention=%dh",
        Config.LOG_DIR.resolve(), Config.LOG_RETENTION_HOURS,
    )


def get_logger(name):
    return logging.getLogger(name)