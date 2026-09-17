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

import pymysql

import well_registry
import well_rules
from config import Config
from logger import get_logger
from mysql_client import MySQLClient
from validation_realtime import alert_raised_at, build_validator

log = get_logger(__name__)


class WellAgent:

    def __init__(self, well):
        self.database_name = well["database_name"]
        self.ip_address = well["ip_address"]

        # Its own logger, so every line this agent writes says which well it
        # is about without each message having to repeat the name.
        self.log = get_logger(f"well_agent.{self.database_name}")

        self.current_activity = "CONNECTING"

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

        # The last failure reported, so a rig that has been unreachable for an
        # hour is one log line and not 720 identical stack traces.
        self._last_failure = None

        # The same relative path GET /alerts reads.
        self.alerts_path = Path("output") / self.database_name / "alerts.json"

        # When the alert file was last swept for old alerts. None sweeps on the
        # first pass, so a file left over from days ago is trimmed at startup.
        self._last_pruned = None

        # ---- what the heartbeat reports ----------------------------------
        # A log that has gone quiet means one of two opposite things: the well
        # is fine and has nothing to say, or it has stopped working. These
        # count what the loop has actually done so the heartbeat can tell them
        # apart without anyone having to guess.
        self._reads = 0
        self._checks = 0
        self._standing = 0
        self._last_heartbeat = None
        self._last_new_row = None

        self.running = True

    # ------------------------------------------------------------------
    def run(self):
        self.log.info(
            "started: %s at %s, table %s, every %gs",
            self.database_name, self.ip_address,
            Config.TABLE_NAME, Config.CHECK_INTERVAL,
        )

        while self.running:

            # Before connecting, so old alerts still clear while the rig is
            # unreachable.
            self._prune_if_due()

            # Before the try as well, so a well that is failing every pass
            # still says so on a schedule rather than going silent after the
            # one line its first failure wrote.
            self._heartbeat()

            try:
                if self.db is None:
                    self.db = MySQLClient(
                        self.ip_address, self.database_name, log=self.log
                    )

                row = self.db.get_current_row()

                self._reads += 1

                if not row:
                    self.log.debug(
                        "%s is empty - nothing to check this pass",
                        Config.TABLE_NAME,
                    )
                    time.sleep(Config.CHECK_INTERVAL)
                    continue

                if self.validator is None:
                    self.validator = build_validator(
                        row, self.rules, self.rules_path, well=self.database_name
                    )

                    mapper = self.validator.mapper

                    self.log.info(
                        "checking %d table column(s): %d mapped, %d not found%s",
                        len(row), len(mapper.resolved), len(mapper.missing),
                        " (" + ", ".join(mapper.missing) + ")"
                        if mapper.missing else "",
                    )

                self._recovered()

                snapshot = json.dumps(row, sort_keys=True, default=str)

                # An unchanged row is the same reading arriving twice, not a
                # new one - checking it again would only repeat the alerts.
                if snapshot != self.last_snapshot:
                    self.last_snapshot = snapshot
                    self._last_new_row = time.monotonic()
                    self._checks += 1

                    result = self.validator.validate(row)

                    self.current_activity = result.activity
                    self._standing = result.standing

                    self.save_alerts(result.alerts)

                # Every successful read, not just a changed one: after a
                # reconnect the row may not have moved yet, and the dashboard
                # should get the activity back without waiting for it to.
                well_registry.set_activity(self.database_name, self.current_activity)

                time.sleep(Config.CHECK_INTERVAL)

            except Exception as exc:
                # Includes a dropped rig link. The connection is thrown away so
                # the next pass builds a fresh one - keeping it would leave the
                # agent looping on a socket that can no longer answer.
                self._report_failure(exc)

                # Not reading, so not knowing: a stale DRILLING on the card
                # would say the rig is on bottom when nobody can see it.
                well_registry.set_activity(self.database_name, None)

                self._disconnect()

                time.sleep(Config.RETRY_INTERVAL)

        self.log.info(
            "stopped after %d read(s) and %d check(s)", self._reads, self._checks,
        )

        self._disconnect()

    # ------------------------------------------------------------------
    def _heartbeat(self):
        """
        "Still here, and this is what I am doing", every HEARTBEAT_SECONDS.

        Without it a silent log is ambiguous in the worst way: a well with
        nothing wrong writes nothing, and so does a well whose thread has died,
        whose rig has gone away, or whose feed has frozen. The reader is left
        guessing at exactly the moment they most need to know.

        The line names the state and carries the counters behind it, so two
        heartbeats next to each other settle it - reads going up means the loop
        is turning, checks going up means the rig is sending new values.
        """
        now = time.monotonic()

        if self._last_heartbeat is not None:
            if now - self._last_heartbeat < Config.HEARTBEAT_SECONDS:
                return

        self._last_heartbeat = now

        state, stale = self._state(now)

        detail = (
            f"{self._reads} read(s), {self._checks} check(s)"
            f" | {self._standing} alert(s) standing"
        )

        if self._last_new_row is not None:
            detail += f" | last new row {now - self._last_new_row:.0f}s ago"

        if self._last_failure is not None:
            (kind, _), count = self._last_failure
            detail += f" | {count} failed pass(es), {kind}"

        # A stale feed is the one state that looks healthy and is not, so it is
        # the one the heartbeat raises its voice about.
        emit = self.log.warning if stale else self.log.info

        emit("alive | %s | %s | %s", state, self.current_activity, detail)

    def _state(self, now):
        """What the agent is doing, and whether that is a problem."""
        if self._last_failure is not None:
            return (
                f"cannot reach {self.ip_address}, retrying every "
                f"{Config.RETRY_INTERVAL:g}s"
            ), False

        if self.db is None:
            return "connecting", False

        if self._last_new_row is None:
            return "connected, no reading yet", False

        idle = now - self._last_new_row

        if idle >= Config.STALE_ROW_SECONDS:
            return (
                f"STALE FEED - the rig is answering but {Config.TABLE_NAME} has "
                f"not changed for {idle:.0f}s, so nothing has been checked in "
                "that time"
            ), True

        return "reading", False

    # ------------------------------------------------------------------
    def _report_failure(self, exc):
        """
        Say what went wrong once, not once every RETRY_INTERVAL.

        A rig outside the network is unreachable for hours at a time, and the
        loop meets that as an exception every few seconds. A full traceback per
        pass buried everything else - one down rig wrote megabytes of identical
        stack. The first failure is reported, and after that only a change of
        failure is, carrying how many passes the last one covered.

        A rig that cannot be reached is a one-line warning even the first time:
        there is nothing in the traceback that "cannot reach 10.0.0.5:3306"
        does not already say. Anything else keeps its traceback, because an
        unexpected error is exactly where the stack is worth having.
        """
        signature = (type(exc).__name__, str(exc))

        if self._last_failure is not None and self._last_failure[0] == signature:
            self._last_failure[1] += 1
            return

        if self._last_failure is not None:
            (kind, detail), count = self._last_failure

            if count > 1:
                self.log.warning(
                    "the previous error repeated on %d more pass(es): %s: %s",
                    count - 1, kind, detail,
                )

        self._last_failure = [signature, 1]

        unreachable = (
            pymysql.err.OperationalError,
            pymysql.err.InterfaceError,
            OSError,
        )

        if isinstance(exc, unreachable):
            self.log.warning(
                "cannot read %s at %s:%s - %s: %s. Retrying every %gs",
                self.database_name, self.ip_address, Config.DB_PORT,
                signature[0], exc, Config.RETRY_INTERVAL,
            )
            return

        # exc_info=exc rather than .exception(): the traceback comes off the
        # exception that was passed in, so this reports correctly whether or
        # not it is called from inside the handler that caught it.
        self.log.error("loop error, reconnecting", exc_info=exc)

    def _recovered(self):
        """Note that the rig is answering again, once."""
        if self._last_failure is None:
            return

        (kind, detail), count = self._last_failure
        self._last_failure = None

        self.log.info(
            "reading again after %d failed pass(es) (%s: %s)", count, kind, detail,
        )

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
            self.log.exception("could not save alerts to %s", self.alerts_path)

    def _prune_if_due(self):
        """Remove alerts older than ALERT_RETENTION_HOURS, at most once an
        ALERT_PRUNE_INTERVAL."""
        now = time.monotonic()

        if self._last_pruned is not None:
            if now - self._last_pruned < Config.ALERT_PRUNE_INTERVAL:
                return

        self._last_pruned = now

        try:
            existing = self._read_alerts()
            kept = self._within_retention(existing)

            # Rewritten only when something actually went.
            if len(kept) == len(existing):
                return

            self._write_alerts(kept)

            self.log.info(
                "removed %d alert(s) older than %gh from %s",
                len(existing) - len(kept),
                Config.ALERT_RETENTION_HOURS, self.alerts_path,
            )

        except Exception:
            self.log.exception(
                "could not remove old alerts from %s", self.alerts_path
            )

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
