"""
When the digest goes out.

One daemon thread, started beside AgentManager in main.py. It wakes every
EMAIL_INTERVAL_SECONDS, works out what each region is owed, sends it and goes
back to sleep. It never touches a rig's database, so a well that cannot be
reached can neither slow it down nor stop it.

The window is per region, and it runs from that region's last *successful*
pass up to now - not from "now minus ten minutes". Two things follow from
that, and both matter:

  - A pass that is late, or one that was missed entirely because the service
    was restarting, leaves no gap. The next window simply starts where the
    last one ended and is longer than usual.

  - A send that fails does not advance the region's watermark, so the window
    stays owed and grows until a send succeeds. Alerts are delayed by a mail
    server being down, never dropped by it.

Per region rather than one watermark for everything, so one region's mail
server refusing does not hold back the others.

Where each region has got to is on disk, in output/email_state.json. Held only
in memory it would be lost on every restart, and the first pass afterwards
would silently skip whatever was raised while the service was down.
"""

import json
import os
import time

from datetime import datetime, timedelta

import well_registry

from config import Config, email_addresses, email_regions, EMAIL_CONFIG_FILE
from logger import get_logger
from mailer import digest, sender

log = get_logger("email-agent")

STATE_FILE = Config.OUTPUT_DIR / "email_state.json"

_STATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


# ---------------------------------------------------------------------------
# Which wells are on which base
# ---------------------------------------------------------------------------

def wells_by_region():
    """
    region (casefolded) -> the wells on that base, and the ones with none.

    The region is a field on the well record, entered beside its IP address,
    rather than a second file mapping names to regions. One source of truth:
    deleting a well takes its region with it, and there is no way for the two
    to disagree about a well that no longer exists.
    """
    grouped = {}
    unassigned = []

    for name, record in well_registry.all_wells().items():
        region = str(record.get("region") or "").strip()

        if not region:
            unassigned.append(name)
            continue

        grouped.setdefault(region.casefold(), []).append(name)

    return grouped, unassigned


# ---------------------------------------------------------------------------
# Where each region has got to
# ---------------------------------------------------------------------------

def read_state():
    """region (casefolded) -> when that region's last sent window ended."""
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            stored = json.load(fh)

    except (OSError, ValueError) as exc:
        # Starting again from now loses at most one window; refusing to start
        # loses every one after it.
        log.warning("%s could not be read (%s) - starting fresh", STATE_FILE.name, exc)
        return {}

    if not isinstance(stored, dict):
        return {}

    state = {}

    for region, text in stored.get("sent_until", {}).items():
        try:
            state[region] = datetime.strptime(text, _STATE_FORMAT)
        except (TypeError, ValueError):
            continue

    return state


def write_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    document = {
        "sent_until": {
            region: moment.strftime(_STATE_FORMAT)
            for region, moment in state.items()
        }
    }

    temporary = STATE_FILE.with_suffix(".json.writing")

    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())

    os.replace(temporary, STATE_FILE)


# ---------------------------------------------------------------------------

class EmailAgent:
    """One thread, every region - alerts are batched per region, not per well."""

    def __init__(self):
        self.running = True

        # region (casefolded) -> when that region's last successfully sent
        # window ended. Read back from disk so a restart resumes rather than
        # skipping whatever happened while it was down.
        self._sent_until = read_state()

        # The last failure reported per region, so a mail server that has been
        # refusing all morning is one line and not one every ten minutes - the
        # same arrangement WellAgent uses for a rig it cannot reach.
        self._last_failure = {}

    def stop(self):
        self.running = False

    def run(self):
        if not sender.configured():
            log.info(
                "Email digests are off (EMAIL_ENABLED / SMTP_HOST / SMTP_FROM "
                "are not all set in .env)"
            )
            return

        log.info(
            "Email agent started - checking every %.0fs, %d region(s) resumed",
            Config.EMAIL_INTERVAL_SECONDS, len(self._sent_until),
        )

        while self.running:
            try:
                self._pass_once()

            except Exception:
                # Anything reaching here is a bug in this file. The loop must
                # not die on it, or the digests stop with no sign of why.
                log.exception("Email pass failed - will retry next interval")

            self._sleep()

    def _sleep(self):
        """
        Wait out the interval in short steps.

        Short steps rather than one long sleep so stop() is acted on within a
        few seconds instead of up to ten minutes, and so the clock being put
        back does not strand the thread asleep.
        """
        until = time.monotonic() + Config.EMAIL_INTERVAL_SECONDS

        while self.running:
            remaining = until - time.monotonic()

            if remaining <= 0:
                return

            time.sleep(min(remaining, 5))

    def _pass_once(self):
        now = datetime.now()

        regions = email_regions()
        grouped, unassigned = wells_by_region()

        if unassigned:
            log.warning(
                "No base region on %d well(s), so they are in no digest: %s",
                len(unassigned), ", ".join(sorted(unassigned)),
            )

        changed = False

        for key, wells in sorted(grouped.items()):
            row = regions.get(key)

            if row is None:
                log.warning(
                    "%d well(s) are on a region with no entry in %s - nothing "
                    "sent for: %s",
                    len(wells), EMAIL_CONFIG_FILE.name, ", ".join(sorted(wells)),
                )
                continue

            if self._send_region(key, row, wells, now):
                changed = True

        if changed:
            write_state(self._sent_until)

    def _window_start(self, key, now):
        """
        Where this region's window begins.

        The end of its last successfully sent window, or one interval ago the
        first time it is seen. Clamped to ALERT_RETENTION_HOURS: after a long
        outage the older alerts have already been swept out of the files, so a
        window reaching further back than that would promise history that is
        not there any more.
        """
        default = now - timedelta(seconds=Config.EMAIL_INTERVAL_SECONDS)
        start = self._sent_until.get(key, default)

        floor = now - timedelta(hours=Config.ALERT_RETENTION_HOURS)

        if start < floor:
            log.warning(
                "Last digest for %s was %s, past the %gh alerts are kept for - "
                "reporting from %s instead",
                key, start, Config.ALERT_RETENTION_HOURS, floor,
            )
            return floor

        return start

    def _send_region(self, key, row, wells, now):
        """
        One region's digest. True if its watermark moved.

        A quiet window moves the watermark too - there was nothing to send, so
        the period is covered and asking about it again next pass would only
        find the same nothing.
        """
        start = self._window_start(key, now)

        digests, _ = digest.build({key: wells}, {key: row}, start, now)

        if not digests:
            self._sent_until[key] = now
            self._recovered(key)
            log.debug("Nothing to report for %s between %s and %s", key, start, now)
            return True

        entry = digests[0]

        try:
            sender.send(
                email_addresses(row["people"]),
                digest.subject(entry),
                digest.text_body(entry),
                digest.html_body(entry),
            )

        except sender.EmailError as exc:
            # Deliberately not advancing. The window stays owed and grows
            # until a send gets through, so a mail server being down delays
            # these alerts rather than losing them.
            self._report_failure(key, exc)
            return False

        # Only once the send has actually returned.
        self._sent_until[key] = now
        self._recovered(key)

        log.info(
            "Digest for %s sent: %d alert(s) on %d well(s), %s to %s",
            entry["region"], entry["total"], len(entry["wells"]),
            start.strftime("%H:%M:%S"), now.strftime("%H:%M:%S"),
        )

        return True

    def _report_failure(self, key, exc):
        message = str(exc)

        if self._last_failure.get(key) != message:
            log.error("Digest for %s not sent: %s", key, message)
            log.info("That window stays owed and is retried next interval")
            self._last_failure[key] = message

    def _recovered(self, key):
        if key in self._last_failure:
            log.info("Email sending for %s recovered", key)
            self._last_failure.pop(key)


def start():
    """Run the loop. Called on its own thread from main.py."""
    EmailAgent().run()


def send_test(to):
    """
    One message, now, to prove the settings work.

    Behind POST /email/test so the Send test button answers with whatever the
    mail server said, rather than leaving someone to find out ten minutes
    later that the password was wrong.
    """
    now = datetime.now()

    return sender.send(
        [to],
        "[DataQC] Test message",
        "\n".join([
            "This is a test from Real-Time Data QC.",
            "",
            "If you can read this, the SMTP settings in .env are working and "
            "the digests will go out.",
            "",
            "Sent {:%d-%m-%Y %H:%M:%S} via {}:{}.".format(
                now, Config.SMTP_HOST, Config.SMTP_PORT
            ),
        ]),
    )
