"""
One agent per well: read the latest row, check it, record what is wrong.

The loop is deliberately dull - connect, read, compare against the last row,
validate if anything moved, sleep. Everything interesting is in the validator.

Alerts are appended to output/<database_name>/alerts.json. They are not
written to any database.
"""

import json
import time

from pathlib import Path

from config import Config
from logger import get_logger
from mysql_client import MySQLClient
from validation_realtime import build_validator

log = get_logger(__name__)


class WellAgent:

    def __init__(self, well):
        self.database_name = well["database_name"]
        self.ip_address = well["ip_address"]

        self.db = None
        self.validator = None

        # The last row as JSON, so an unchanged row can be skipped without
        # running every check against it again.
        self.last_snapshot = None

        self.running = True

    # ------------------------------------------------------------------
    def run(self):
        log.info("Agent started: %s (%s)", self.database_name, self.ip_address)

        while self.running:

            try:
                if self.db is None:
                    self.db = MySQLClient(self.ip_address, self.database_name)

                row = self.db.get_current_row()

                if not row:
                    log.debug("%s: table is empty", self.database_name)
                    time.sleep(Config.CHECK_INTERVAL)
                    continue

                if self.validator is None:
                    self.validator = build_validator(row)

                snapshot = json.dumps(row, sort_keys=True, default=str)

                # An unchanged row is the same reading arriving twice, not a
                # new one - checking it again would only repeat the alerts.
                if snapshot != self.last_snapshot:
                    self.last_snapshot = snapshot

                    result = self.validator.validate(row)

                    self.save_alerts(result.alerts)

                time.sleep(Config.CHECK_INTERVAL)

            except Exception:
                # Includes a dropped rig link. The connection is thrown away so
                # the next pass builds a fresh one - keeping it would leave the
                # agent looping on a socket that can no longer answer.
                log.exception("%s: loop error, reconnecting", self.database_name)

                self._disconnect()

                time.sleep(Config.RETRY_INTERVAL)

        log.info("Agent stopped: %s", self.database_name)

        self._disconnect()

    # ------------------------------------------------------------------
    def save_alerts(self, alerts):

        if not alerts:
            return

        file_path = (
            Path("output")
            / self.database_name
            / "alerts.json"
        )

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        try:

            if file_path.exists():

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    existing = json.load(f)

            else:

                existing = []

            existing.extend(alerts)

            with open(
                file_path,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    existing,
                    f,
                    indent=4
                )

        except Exception as e:

            print(
                f"Alert save error: {e}"
            )

    # ------------------------------------------------------------------
    def _disconnect(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def stop(self):
        """Ask the loop to finish. It closes its own connection on the way out."""
        self.running = False
