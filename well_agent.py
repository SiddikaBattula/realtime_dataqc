"""
One agent per well: read the latest row, check it, record what is wrong.

The loop is deliberately dull - connect, read, compare against the last row,
validate if anything moved, sleep. Everything interesting is in the validator.

The rules it checks against are this well's own - entered in the dashboard and
kept in data/wells/ - so two wells on the same server can have quite different
ranges, activity flags and column names.

Alerts are appended to output/<database_name>/alerts.json. They are not
written to any database. Alerts older than ALERT_RETENTION_HOURS are removed
from that file by the same agent, so nothing else ever writes to it.
"""

import json
import time

from datetime import datetime, timedelta
from pathlib import Path

import well_registry
import well_rules
from config import Config
from logger import get_logger
from mysql_client import MySQLClient
from validation_realtime import alert_raised_at, build_validator

log = get_logger(__name__)

# Seconds between sweeps of the alert file for alerts past their retention.
# Saving a new alert sweeps too; this is what clears them on a quiet well.
PRUNE_INTERVAL = 60


class WellAgent:

    def __init__(self, well):
        self.database_name = well["database_name"]
        self.ip_address = well["ip_address"]

        self.validator = None
        self.current_activity = "CONNECTING"
        self.last_snapshot = None
        # This well's own four rule blocks, and the file they came from. The
        # validator re-reads that file when it changes, so editing the well in
        # the dashboard takes effect without restarting its agent.
        self.rules = well["rules"]
        self.rules_path = well_rules.path_for(self.database_name)

        self.db = None
        self.validator = None

        # The last row as JSON, so an unchanged row can be skipped without
        # running every check against it again.
        self.last_snapshot = None

        # The same relative path GET /alerts reads.
        self.alerts_path = Path("output") / self.database_name / "alerts.json"

        # When the alert file was last swept for old alerts. None sweeps on the
        # first pass, so a file left over from days ago is trimmed at startup.
        self._last_pruned = None

        self.running = True

    # ------------------------------------------------------------------
    def run(self):
        log.info("Agent started: %s (%s)", self.database_name, self.ip_address)

        while self.running:

            # Before connecting, so old alerts still clear while the rig is
            # unreachable.
            self._prune_if_due()

            try:
                if self.db is None:
                    self.db = MySQLClient(self.ip_address, self.database_name)

                row = self.db.get_current_row()

                if not row:
                    log.debug("%s: table is empty", self.database_name)
                    time.sleep(Config.CHECK_INTERVAL)
                    continue

                if self.validator is None:
                    self.validator = build_validator(
                        row, self.rules, self.rules_path
                    )

                snapshot = json.dumps(row, sort_keys=True, default=str)

                # An unchanged row is the same reading arriving twice, not a
                # new one - checking it again would only repeat the alerts.
                if snapshot != self.last_snapshot:
                    self.last_snapshot = snapshot

                    result = self.validator.validate(row)

                    self.current_activity = result.activity

                    self.save_alerts(result.alerts)

                # Every successful read, not just a changed one: after a
                # reconnect the row may not have moved yet, and the dashboard
                # should get the activity back without waiting for it to.
                well_registry.set_activity(self.database_name, self.current_activity)

                time.sleep(Config.CHECK_INTERVAL)

            except Exception:
                # Includes a dropped rig link. The connection is thrown away so
                # the next pass builds a fresh one - keeping it would leave the
                # agent looping on a socket that can no longer answer.
                log.exception("%s: loop error, reconnecting", self.database_name)

                # Not reading, so not knowing: a stale DRILLING on the card
                # would say the rig is on bottom when nobody can see it.
                well_registry.set_activity(self.database_name, None)

                self._disconnect()

                time.sleep(Config.RETRY_INTERVAL)

        log.info("Agent stopped: %s", self.database_name)

        self._disconnect()

    # ------------------------------------------------------------------
    def save_alerts(self, alerts):

        if not alerts:
            return

        try:
            # The file is being rewritten anyway, so old alerts go now.
            existing = self._within_retention(self._read_alerts())

            existing.extend(alerts)

            self._write_alerts(existing)

        except Exception:
            log.exception("%s: could not save alerts", self.database_name)

    def _prune_if_due(self):
        """Remove alerts older than ALERT_RETENTION_HOURS, at most once a PRUNE_INTERVAL."""
        now = time.monotonic()

        if self._last_pruned is not None and now - self._last_pruned < PRUNE_INTERVAL:
            return

        self._last_pruned = now

        try:
            existing = self._read_alerts()
            kept = self._within_retention(existing)

            # Rewritten only when something actually went.
            if len(kept) == len(existing):
                return

            self._write_alerts(kept)

            log.info(
                "%s: removed %d alert(s) older than %gh from %s",
                self.database_name, len(existing) - len(kept),
                Config.ALERT_RETENTION_HOURS, self.alerts_path,
            )

        except Exception:
            log.exception("%s: could not remove old alerts", self.database_name)

    @staticmethod
    def _within_retention(alerts):
        """
        The alerts raised within ALERT_RETENTION_HOURS.

        One with no readable timestamp is dropped as well: it could never age
        out, so it would sit in the file forever.
        """
        cutoff = datetime.now() - timedelta(hours=Config.ALERT_RETENTION_HOURS)

        return [
            alert for alert in alerts
            if (raised := alert_raised_at(alert)) is not None and raised >= cutoff
        ]

    def _read_alerts(self):
        if not self.alerts_path.exists():
            return []

        with open(self.alerts_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_alerts(self, alerts):
        self.alerts_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.alerts_path, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=4)

    # ------------------------------------------------------------------
    def _disconnect(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def stop(self):
        """Ask the loop to finish. It closes its own connection on the way out."""
        self.running = False
