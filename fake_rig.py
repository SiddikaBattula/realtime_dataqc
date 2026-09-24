"""
TEMPORARY test harness - a fake rig in place of the real MySQL server.

Nothing in the app imports this file; delete it when you are done with it.

The rig at 43.241.39.241:8100 is unreachable from here, so every agent spends
its life in the "cannot read ... Retrying every 5s" branch and none of the
checks ever run. This replaces MySQLClient with a rig that is always up and
feeds the real WellAgent / RealtimeValidator / AgentManager a scripted series
of readings, so each feature can be watched doing its job.

Three ways to run it (use the project's interpreter, .venv/Scripts/python.exe):

    python main.py                     the real app, with every saved well fed
                                       simulated readings - this is what fills
                                       the dashboard. Needs FAKE_RIG=1, which
                                       is in .env; remove that line and main.py
                                       reads the real rigs again, untouched.
    python fake_rig.py                 scripted run, then a PASS/FAIL table
    python fake_rig.py --live          the same as FAKE_RIG=1 main.py, but on a
                                       throwaway well called TEST-RIG that
                                       leaves data/wells/ alone
    python fake_rig.py --real          scripted run at the real .env timings
                                       (~8 minutes; the default squeezes the
                                       same script into about two)

Behind main.py the script loops at the timings in .env, and three of its
phases are left out: rewriting a well's rules, killing the agent thread, and
the NULL bit depth phase. They belong in a test run, not in an app someone is
watching. Run this file directly for those.

What the script walks through, in order, and what each phase is there to prove:

    healthy          connects, reads, maps columns, activity = DRILLING
    range breach     H2S driven above its max            -> "above limit"
    zero in DRILLING RPM dropped to 0 while on bottom    -> "cannot be 0"
    bit depth jump   both depths jump 8 m                -> "Bit Depth jump"
    non drilling     bit lifted past drilling_criteria   -> activity flips
    ROP surge        ROP up past its threshold, held     -> "increased by"
    hookload stuck   HOOKLOAD frozen, other values move  -> "remained unchanged"
    TA over TG       TA held above TG past its window    -> "is greater than"
    rules edited     H2S max rewritten to 1 mid-run      -> "Reloaded rules"
    empty table      the table answers with no row       -> "nothing to check"
    stale feed       the identical row over and over     -> heartbeat STALE FEED
    outage           the same timeout you see in the log -> one warning, then
                                                            a repeat count
    recovered        the rig answers again               -> "reading again after"
    no bit depth     BIT_DPT_MD comes back NULL          -> activity undetermined
    crash            an error the loop cannot catch      -> manager restarts it

It also seeds an alert two days old before it starts, so the retention sweep
has something to remove, and it samples what GET /wells reports as the
activity while the script runs.

The well is called TEST-RIG and its record is written to a temp directory, not
to data/wells/ - your real wells are neither read for the run nor touched. Its
alerts go to output/TEST-RIG/alerts.json, which is where the dashboard reads
them from.
"""

import argparse
import json
import logging
import random
import re
import shutil
import sys
import tempfile
import threading
import time

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pymysql

import mysql_client
import rule_files
import well_agent
import well_registry
import well_rules

from column_mapper import to_number
from config import Config
from logger import get_logger, setup_logging
from validation_realtime import ALERT_TIME_FORMAT, INVERSE_PARAMS

log = get_logger("fake_rig")

WELL = "TEST-RIG"
FAKE_IP = "127.0.0.1"

# The address the fake outage names, so the warning reads exactly like the one
# in your log rather than like a local failure.
REAL_IP = "43.241.39.241"

ALERTS_PATH = Path("output") / WELL / "alerts.json"

# Readings, in the unit each parameter's limits are written in - the fake rig
# divides by that parameter's `factor` on the way out, so these stay readable
# whatever the rig stores. Anything mapped but not named here gets the middle
# of its range, or a small positive number when it has no range.
BASE = {
    "DEPTH": 2500.0,
    "ROP": 25.0,
    "HOOKLOAD": 100.0,
    "WOB": 15.0,
    "RPM": 120.0,
    "SPP": 2500.0,
    "TG": 2.0,
    "TA": 1.0,
    "MW_IN": 10.0,
    "MW_OUT": 10.2,
    "TEMP_IN": 40.0,
    "TEMP_OUT": 45.0,
    "H2S": 5.0,
    "LEL": 0.2,
    "Co2": 0.1,
    "MP1_SPM": 60.0,
    "MP2_SPM": 58.0,
    "MP3_SPM": 0.0,
    "MP4_SPM": 0.0,
    "MP5_SPM": 0.0,
    "ROT_SPEED": 120.0,
    "FLOW_IN": 1500.0,
    "PitSumVol1": 250.0,
    "RETURNS": 1480.0,
    "ROT_TORQUE_AVG": 12.0,
    "CONDUCT_IN": 4.5,
    "CONDUCT_OUT": 4.8,
}

# Metres of hole depth minus bit depth in the healthy phases. Under any sane
# drilling_criteria this is on bottom.
ON_BOTTOM = 0.05

# What the loop runs on while the script plays, so two minutes covers what
# would otherwise take a quarter of an hour. --real leaves .env alone.
FAST_CONFIG = {
    "CHECK_INTERVAL": 0.5,
    "RETRY_INTERVAL": 2.0,
    "HEARTBEAT_SECONDS": 5.0,
    "STALE_ROW_SECONDS": 10.0,
    "AGENT_POLL_INTERVAL": 3.0,
    "ALERT_PRUNE_INTERVAL": 5.0,
}

# The same for the well's own rules: the windows the change checks measure
# over. --real leaves the well's real numbers in place.
FAST_CONDITIONS = {
    "TA_TG": 8,
    "SPP": 4,
    "SPM": 4,
    "ROP": 4,
    "HOOKLOAD": 8,
}


class RigCrash(BaseException):
    """
    An error WellAgent.run cannot catch.

    Deliberately not an Exception: the loop handles those and reconnects. This
    is what a genuine bug in the loop would do - end the thread - and it is
    the only way to see AgentManager._collect_dead notice and restart it.
    """


# ---------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------
@dataclass
class Phase:
    name: str
    fast: float
    slow: float
    note: str
    mutate: object = None          # f(feed, row) -> None, per reading
    mode: str = "normal"           # normal | empty | freeze | outage | crash
    on_enter: object = None        # f() -> None, once as the phase starts

    # Left out when the fake rig is driving main.py. Three phases are fine in
    # a two-minute test run and wrong in an app someone is watching: one
    # rewrites the well's rules on disk, one kills the agent thread, and one
    # trips the NULL bit depth bug into a traceback a second.
    standalone: bool = False

    def seconds(self, fast):
        return self.fast if fast else self.slow


class FakeRigFeed:
    """
    One rig's table, made up as it goes.

    Columns come from the well's own column_mapping (the first alias of each
    logical name), so the mapper resolves every one of them, and the readings
    are in whatever unit that well's `factor` implies.
    """

    def __init__(self, rules):
        self.ranges = rules.get("ranges", {})
        self.columns = {
            logical: aliases[0]
            for logical, aliases in rules["column_mapping"].items()
            if aliases
        }
        self.random = random.Random(20260922)

        self.phase = None
        self.frozen = None
        self.crashed = False

    # -- units ---------------------------------------------------------
    def raw(self, param, value):
        """`value`, as the rig's own column would hold it."""
        factor = to_number(self.ranges.get(param, {}).get("factor"))

        if not factor:
            return round(value, 4)

        if param.upper() in INVERSE_PARAMS:
            if value == 0:
                return 0.0
            return round(factor / value, 4)

        return round(value / factor, 4)

    def default(self, param):
        if param in BASE:
            return BASE[param]

        limits = self.ranges.get(param)

        if limits:
            low = to_number(limits.get("min")) or 0.0
            high = to_number(limits.get("max"))

            if high is None:
                high = low + 10

            return low + (high - low) / 2

        return 1.0

    def set(self, row, param, value):
        """Put `value` (in limit units) into the column `param` maps to."""
        column = self.columns.get(param)

        if column is None:
            return

        row[column] = None if value is None else self.raw(param, value)

    # -- phases --------------------------------------------------------
    def enter(self, phase):
        self.phase = phase
        self.frozen = None

    def gate(self):
        """Raise whatever this phase says the connection is doing."""
        if self.phase is None:
            return

        if self.phase.mode == "outage":
            raise pymysql.err.OperationalError(
                2003,
                "Can't connect to MySQL server on '%s' (timed out)" % REAL_IP,
            )

        if self.phase.mode == "crash" and not self.crashed:
            self.crashed = True
            raise RigCrash("simulated bug in the read - the loop cannot catch this")

    def row(self):
        """The single row the table holds, this instant."""
        if self.phase is not None and self.phase.mode == "empty":
            return None

        if self.phase is not None and self.phase.mode == "freeze":
            if self.frozen is None:
                self.frozen = self._build()

            return dict(self.frozen)

        return self._build()

    def _build(self):
        row = {}

        for param in self.columns:
            if param in ("TIME", "BIT_DPT_MD", "DEPTH", "SPP", "HOOKLOAD"):
                continue

            self.set(row, param, self.default(param))

        # A touch of movement, so every reading is a new one and a healthy rig
        # is not mistaken for a stalled feed. HOOKLOAD has to be among them:
        # its check alerts on a value that has not moved at all, so a hookload
        # held at exactly 100 would raise "remained unchanged" in every phase
        # and the stuck phase would prove nothing.
        depth = BASE["DEPTH"] + self.random.uniform(-0.01, 0.01)

        self.set(row, "DEPTH", depth)
        self.set(row, "BIT_DPT_MD", depth - ON_BOTTOM)
        self.set(row, "SPP", BASE["SPP"] + self.random.uniform(-5, 5))
        self.set(row, "HOOKLOAD", BASE["HOOKLOAD"] + self.random.uniform(-0.5, 0.5))

        if "TIME" in self.columns:
            row[self.columns["TIME"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # A column the mapping does not mention, so the startup audit has an
        # unmapped column to report on.
        row["RIG_MODE"] = "TEST"

        if self.phase is not None and self.phase.mutate is not None:
            self.phase.mutate(self, row)

        return row


class FeedPool:
    """
    One feed per well, all of them on the same phase of the script.

    Started from main.py there may be several wells in the registry, and two
    rigs can name their columns quite differently - a single feed would serve
    one well's column names to all of them. Each well gets a feed built from
    its own rules instead, and the script moves them through the phases
    together, so the dashboard shows every card doing the same thing at once.
    """

    def __init__(self, rules_for):
        self.rules_for = rules_for
        self.feeds = {}
        self.phase = None
        self.guard = threading.Lock()

    def feed_for(self, database_name):
        with self.guard:
            feed = self.feeds.get(database_name)

            if feed is None:
                feed = FakeRigFeed(self.rules_for(database_name))
                feed.enter(self.phase)
                self.feeds[database_name] = feed

            return feed

    def enter(self, phase):
        with self.guard:
            self.phase = phase

            for feed in self.feeds.values():
                feed.enter(phase)


class FakeRig:
    """Stands in for MySQLClient - the same three methods the agent calls."""

    pool = None          # set by install()

    def __init__(self, ip_address, database_name, log=None):
        self.log = log or get_logger("fake_rig.client")
        self.database_name = database_name
        self.feed = FakeRig.pool.feed_for(database_name)

        self.feed.gate()

        self.log.info(
            "connected to %s on %s:%s in 2ms (FAKE RIG - no real database)",
            database_name, ip_address, Config.DB_PORT,
        )

    def get_current_row(self):
        self.feed.gate()
        return self.feed.row()

    def save_alert(self, alert_time, description):
        self.log.info("fake rig ignoring save_alert: %s", description)

    def close(self):
        self.log.debug("fake connection to %s closed", self.database_name)


def install(pool):
    """Put the fake rig in front of every agent."""
    FakeRig.pool = pool

    # well_agent imported the name directly, so both bindings are replaced.
    well_agent.MySQLClient = FakeRig
    mysql_client.MySQLClient = FakeRig


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------
def _lift(metres):
    """Pick the bit up off bottom - NON DRILLING once past drilling_criteria."""
    def mutate(feed, row):
        depth = to_number(row.get(feed.columns.get("DEPTH")))

        if depth is None:
            depth = BASE["DEPTH"]

        feed.set(row, "BIT_DPT_MD", depth - metres)

    return mutate


def _jump(metres):
    """Move both depths at once - a jump, still on bottom."""
    def mutate(feed, row):
        feed.set(row, "DEPTH", BASE["DEPTH"] - metres)
        feed.set(row, "BIT_DPT_MD", BASE["DEPTH"] - metres - ON_BOTTOM)

    return mutate


def _set(param, value):
    def mutate(feed, row):
        feed.set(row, param, value)

    return mutate


def _both(first, second):
    def mutate(feed, row):
        first(feed, row)
        second(feed, row)

    return mutate


def _freeze_hookload(feed, row):
    # Everything else keeps moving; only HOOKLOAD stands still, which is what
    # the stalled-feed check is looking for.
    feed.set(row, "HOOKLOAD", BASE["HOOKLOAD"])


def _no_bit_depth(feed, row):
    column = feed.columns.get("BIT_DPT_MD")

    if column:
        row[column] = None


def build_script(tighten_h2s=None):
    """
    The whole script. Without `tighten_h2s` the rules-edit phase is left out,
    because there is nothing safe to rewrite - see dashboard_script.
    """
    script = [
        Phase("healthy", 8, 20,
              "connects, resolves columns, DRILLING with nothing wrong"),

        Phase("range breach", 5, 10,
              "H2S above its max -> a range alert",
              mutate=_set("H2S", 80.0)),

        Phase("zero in DRILLING", 5, 10,
              "RPM at 0 while on bottom -> an activity zero alert",
              mutate=_set("RPM", 0.0)),

        Phase("bit depth jump", 5, 10,
              "both depths jump 8 m -> a bit depth jump alert",
              mutate=_jump(8.0)),

        Phase("non drilling", 6, 15,
              "bit lifted 3 m off bottom -> activity flips to NON DRILLING",
              mutate=_lift(3.0)),

        Phase("ROP surge", 9, 20,
              "ROP up 80% and held -> a ROP change alert",
              mutate=_set("ROP", 45.0)),

        Phase("hookload stuck", 12, 45,
              "HOOKLOAD frozen while the rest moves -> a stalled feed alert",
              mutate=_freeze_hookload),

        Phase("TA over TG", 12, 110,
              "TA held above TG -> a TA>TG alert once its window passes",
              mutate=_both(_set("TA", 6.0), _set("TG", 2.0))),

        Phase("rules edited", 6, 10,
              "H2S max rewritten to 1 mid-run -> rules reloaded, no restart",
              on_enter=tighten_h2s, standalone=True),

        Phase("empty table", 4, 8,
              "the table answers with no row at all",
              mode="empty"),

        Phase("stale feed", 16, 150,
              "the identical row, over and over -> the heartbeat says STALE FEED",
              mode="freeze"),

        Phase("outage", 12, 30,
              "the timeout from your log -> one warning, then a repeat count",
              mode="outage"),

        Phase("recovered", 8, 15,
              "the rig answers again -> reading again after N failed pass(es)"),

        Phase("no bit depth", 6, 10,
              "BIT_DPT_MD comes back NULL -> activity cannot be determined",
              mutate=_no_bit_depth, standalone=True),

        Phase("crash", 8, 15,
              "an error the loop cannot catch -> the manager restarts the agent",
              mode="crash", standalone=True),
    ]

    if tighten_h2s is None:
        script = [phase for phase in script if phase.on_enter is None]

    return script


def dashboard_script():
    """
    The phases worth watching on the dashboard, at the real .env timings.

    Everything that only makes sense in a one-off test run is dropped, so this
    can loop behind main.py all day without editing a rule file, killing a
    thread or filling the log with tracebacks.
    """
    return [phase for phase in build_script() if not phase.standalone]


# ---------------------------------------------------------------------------
# Watching what happened
# ---------------------------------------------------------------------------
class LogCapture(logging.Handler):
    """
    Every line the app logs, kept so the checks can look one up.

    This harness's own lines are dropped rather than kept: the phase banner
    says what each phase is meant to produce, in the same words the app uses,
    and a check that matched the banner would pass on its own commentary.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []
        self.guard = threading.Lock()

    def emit(self, record):
        if record.name.startswith("fake_rig"):
            return

        try:
            line = "%s|%s|%s" % (record.levelname, record.name, record.getMessage())
        except Exception:
            return

        with self.guard:
            self.lines.append(line)

    def find(self, *needles):
        with self.guard:
            for line in self.lines:
                if all(needle in line for needle in needles):
                    return line

        return None

    def count(self, *needles):
        with self.guard:
            return sum(
                1 for line in self.lines
                if all(needle in line for needle in needles)
            )


def read_alerts():
    if not ALERTS_PATH.exists():
        return []

    try:
        with open(ALERTS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)

    except (OSError, json.JSONDecodeError):
        return []


def find_alert(alerts, *needles):
    for alert in alerts:
        if all(needle in alert for needle in needles):
            return alert

    return None


def seed_old_alert():
    """An alert two days old, for the retention sweep to find and remove."""
    stamp = (datetime.now() - timedelta(hours=48)).strftime(ALERT_TIME_FORMAT)
    old = "[%s] SEEDED two days ago - the retention sweep should remove this" % stamp

    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(ALERTS_PATH, "w", encoding="utf-8") as fh:
        json.dump([old], fh, indent=4)

    return old


# ---------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------
def load_rules(prefer_saved, fast):
    """
    The rules the test well runs on: a real well's, if there is one on disk.

    Testing against a saved well's own rules is the point - its column names,
    its limits and its activity flags are what an agent actually meets. The
    template in data/ is the fallback for a first run with no wells saved.
    """
    source = "the rule template in data/"
    rules = None

    if prefer_saved:
        for path in sorted((Config.DATA_DIR / "wells").glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    record = json.load(fh)

            except (OSError, json.JSONDecodeError):
                continue

            if isinstance(record, dict) and isinstance(record.get("rules"), dict):
                rules = record["rules"]
                source = "%s (%s)" % (record.get("database_name", path.stem), path.name)
                break

    if rules is None:
        rules = rule_files.template()

    # A copy: the well on disk is read and then left entirely alone.
    rules = json.loads(json.dumps(rules))

    rules.setdefault("drilling_criteria", rule_files.DEFAULT_DRILLING_CRITERIA)
    rules.setdefault("bit_depth_threshold", rule_files.DEFAULT_BIT_DEPTH_THRESHOLD)

    if fast:
        for name, seconds in FAST_CONDITIONS.items():
            block = rules.get("conditions", {}).get(name)

            if isinstance(block, dict) and "duration_seconds" in block:
                block["duration_seconds"] = seconds

    return rules, source


def isolate_wells_dir():
    """
    Point well_rules at a temp directory for the run.

    The test well is saved, reloaded and deleted like any other well, and
    data/wells/ - your real ones - is neither written to nor started from.
    """
    sandbox = Path(tempfile.gettempdir()) / "dataqc_fake_rig_wells"

    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    well_rules.WELLS_DIR = sandbox

    return sandbox


def quiet_thread_crash():
    """
    Report the deliberate crash as one line rather than a bare traceback.

    A thread that dies prints through threading.excepthook, and the point of
    the crash phase is the manager's restart, not the stack behind it.
    """
    previous = threading.excepthook

    def hook(args):
        if issubclass(args.exc_type, RigCrash):
            log.info(
                "agent thread %s ended on the simulated crash, as intended",
                getattr(args.thread, "name", "?"),
            )
            return

        previous(args)

    threading.excepthook = hook


# ---------------------------------------------------------------------------
# Running the script
# ---------------------------------------------------------------------------
def play(pool, script, fast, activities, once=True):
    while True:
        for phase in script:
            pool.enter(phase)

            log.info("")
            log.info("=" * 78)
            log.info("PHASE: %s - %s", phase.name.upper(), phase.note)
            log.info("=" * 78)

            if phase.on_enter is not None:
                try:
                    phase.on_enter()
                except Exception:
                    log.exception("phase %s could not start", phase.name)

            deadline = time.monotonic() + phase.seconds(fast)

            while time.monotonic() < deadline:
                time.sleep(0.25)

                # What GET /wells would say right now - the dashboard's view
                # of this well, sampled as the script runs.
                summary = well_registry.summaries().get(WELL)

                if summary:
                    activities.add(summary.get("activity"))

        if once:
            return


def outage_was_summarised(capture):
    """
    One warning for the whole outage, however many passes it covered.

    The count is read back off the recovery line rather than counted here:
    that is the agent's own tally of how many passes failed behind the single
    warning, which is the thing worth checking.
    """
    recovery = capture.find("reading again after", "failed pass(es)")

    if recovery is None:
        return None

    match = re.search(r"reading again after (\d+) failed", recovery)

    if match is None:
        return None

    failed = int(match.group(1))
    warnings = capture.count("cannot read TEST-RIG")

    if failed < 2 or warnings != 1:
        return None

    return "%d failed pass(es) reported as %d warning(s)" % (failed, warnings)


def evaluate(capture, activities, seeded):
    alerts = read_alerts()

    def check(title, evidence, absent=False, hint=None):
        passed = (evidence is None) if absent else (evidence is not None)

        return {
            "title": title,
            "passed": passed,
            "evidence": evidence,
            "hint": hint,
        }

    results = [
        check("connects and reads the table",
              capture.find("connected to TEST-RIG")),
        check("columns resolved against the well's own mapping",
              capture.find("checking", "column(s)")),
        check("activity worked out as DRILLING",
              "GET /wells reported DRILLING" if "DRILLING" in activities else None),
        check("activity flips to NON DRILLING off bottom",
              "GET /wells reported NON DRILLING"
              if "NON DRILLING" in activities else None),
        check("range check alerts above the max",
              find_alert(alerts, "above limit")),
        check("zero check alerts on 0 while DRILLING",
              find_alert(alerts, "cannot be 0 in DRILLING")),
        check("bit depth jump alert",
              find_alert(alerts, "Bit Depth jump")),
        check("ROP change alert",
              find_alert(alerts, "increased by")),
        check("stalled HOOKLOAD alert",
              find_alert(alerts, "remained unchanged for")),
        check("TA above TG alert",
              find_alert(alerts, "is greater than")),
        check("rules reloaded mid-run, without a restart",
              capture.find("Reloaded rules from")),
        check("empty table handled without an error",
              capture.find("nothing to check this pass")),
        check("heartbeat while running",
              capture.find("alive |")),
        check("stale feed called out by the heartbeat",
              capture.find("STALE FEED")),
        check("outage reported as one warning, not a traceback",
              capture.find("cannot read TEST-RIG", "Retrying every")),
        check("one warning for the whole outage, passes counted",
              outage_was_summarised(capture)),
        check("recovery reported once the rig answers",
              capture.find("reading again after")),
        check("activity cleared while the rig is unreachable",
              "GET /wells reported no activity" if None in activities else None),
        check("alerts written to output/TEST-RIG/alerts.json",
              "%d alert(s) in the file" % len(alerts) if alerts else None),
        check("retention sweep removed the two-day-old alert",
              capture.find("alert(s) older than") if seeded not in alerts else None),
        check("NULL bit depth reported as undetermined activity",
              find_alert(alerts, "Cannot determine activity"),
              hint="the alert is raised, then lost with the reading that "
                   "raised it - see the next line"),
        check("NULL bit depth did NOT break the check loop",
              capture.find("loop error, reconnecting"), absent=True,
              hint="validation_realtime.py, the depth jump check: "
                   "abs(bit_depth - self.last_bit_depth) with bit_depth None "
                   "raises TypeError out of validate(), so the whole reading "
                   "is thrown away and the agent reconnects every pass"),
        check("dead agent thread noticed and restarted",
              capture.find("has died - restarting")),
    ]

    return results, alerts


def report(results, alerts, source, fast):
    width = max(len(item["title"]) for item in results) + 2

    print()
    print("=" * 110)
    print("  FEATURE CHECK - well %s, rules from %s, %s timings"
          % (WELL, source, "fast" if fast else "real .env"))
    print("=" * 110)

    for item in results:
        mark = "PASS" if item["passed"] else "FAIL"
        evidence = item["evidence"] or "nothing matched"
        evidence = evidence if len(evidence) <= 96 else evidence[:93] + "..."

        print("  [%s] %s %s" % (mark, item["title"].ljust(width), evidence))

        if not item["passed"] and item.get("hint"):
            print("         %s-> %s" % (" " * width, item["hint"]))

    failed = [item for item in results if not item["passed"]]

    print("-" * 110)
    print("  %d/%d passed%s" % (
        len(results) - len(failed), len(results),
        "" if not failed else " - failed: " + ", ".join(i["title"] for i in failed),
    ))
    print("  %d alert(s) in %s" % (len(alerts), ALERTS_PATH))
    print("  full detail in %s and %s"
          % (Config.LOG_DIR / "app.log", Config.LOG_DIR / "alerts.log"))
    print("=" * 110)
    print()

    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# Driving main.py
# ---------------------------------------------------------------------------
DEMO_WELL = "FAKE-RIG-1"


def _rules_from_registry(database_name):
    """Whatever rules that well is saved with, so its own columns are served."""
    record = well_registry.get(database_name)

    if record and isinstance(record.get("rules"), dict):
        return record["rules"]

    return rule_files.template()


def _wait_for_wells(seconds=20):
    """
    Give the API's startup time to bring the saved wells back.

    attach() runs before uvicorn does, so the registry is empty for the first
    moment of a run and a card that has not appeared yet is not the same thing
    as no wells at all.
    """
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if well_registry.count():
            return True

        time.sleep(0.5)

    return False


def _drive(pool):
    """The script, looping behind main.py until the process stops."""
    if not _wait_for_wells():
        # Nothing saved, so nothing would appear on the dashboard at all. One
        # well is added on the template's rules, and it is an ordinary well -
        # delete it from the dashboard like any other.
        log.warning(
            "No wells are being monitored, so there would be nothing to see - "
            "adding %s on the template rules. Delete it from the dashboard "
            "when you are done.", DEMO_WELL,
        )

        try:
            well_registry.add(DEMO_WELL, FAKE_IP, rule_files.template())
        except Exception:
            log.exception("could not add %s", DEMO_WELL)
            return

    log.info(
        "Fake rig driving %d well(s): %s",
        well_registry.count(), ", ".join(well_registry.all_wells()),
    )

    play(pool, dashboard_script(), fast=False, activities=set(), once=False)


def attach():
    """
    Run this process's agents against the fake rig instead of a real one.

    main.py calls this when FAKE_RIG is set in the environment, and nothing at
    all happens without it - no import of this module changes behaviour on its
    own. Every well in the registry is served readings built from its own
    rules, at the timings in .env, so the dashboard behaves exactly as it
    would on a rig that was reachable.
    """
    log.warning("=" * 78)
    log.warning("FAKE RIG ENABLED - readings are simulated and no rig is read")
    log.warning("Unset FAKE_RIG to go back to the real databases")
    log.warning("=" * 78)

    pool = FeedPool(_rules_from_registry)

    install(pool)

    threading.Thread(target=_drive, args=(pool,), name="fake-rig", daemon=True).start()

    return pool


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Run the QC agent against a fake rig instead of the real one.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="run the whole app - dashboard, API, agent manager - on the fake "
             "rig, looping the script until Ctrl-C",
    )
    parser.add_argument(
        "--real", action="store_true",
        help="keep .env's own intervals and the well's own change windows "
             "(slower, closer to production)",
    )
    parser.add_argument(
        "--template", action="store_true",
        help="use data/'s rule template instead of a saved well's rules",
    )
    parser.add_argument(
        "--port", type=int, default=Config.CONFIG_API_PORT,
        help="port for --live (default: CONFIG_API_PORT from .env)",
    )
    args = parser.parse_args()

    fast = not args.real

    setup_logging()

    capture = LogCapture()
    logging.getLogger().addHandler(capture)

    if fast:
        for name, value in FAST_CONFIG.items():
            setattr(Config, name, value)

    rules, source = load_rules(prefer_saved=not args.template, fast=fast)

    sandbox = isolate_wells_dir()

    log.info("Fake rig harness: well %s, rules from %s", WELL, source)
    log.info("Well records for this run live in %s (removed on exit)", sandbox)
    log.info(
        "Timings: check %gs, retry %gs, heartbeat %gs, stale %gs, manager %gs",
        Config.CHECK_INTERVAL, Config.RETRY_INTERVAL, Config.HEARTBEAT_SECONDS,
        Config.STALE_ROW_SECONDS, Config.AGENT_POLL_INTERVAL,
    )

    seeded = seed_old_alert()

    # One well, one set of rules - whatever it is called, it gets these.
    pool = FeedPool(lambda database_name: rules)

    install(pool)
    quiet_thread_crash()

    def tighten_h2s():
        """Rewrite the well's rules while its agent is running."""
        edited = json.loads(json.dumps(rules))
        edited.setdefault("ranges", {}).setdefault("H2S", {})["max"] = 1

        well_registry.add(WELL, FAKE_IP, edited)

        log.info("H2S max rewritten to 1 - the agent should pick that up itself")

    well_registry.add(WELL, FAKE_IP, rules)

    from agent_manager import AgentManager

    threading.Thread(
        target=AgentManager().start, name="agent-manager", daemon=True,
    ).start()

    script = build_script(tighten_h2s)
    activities = set()

    try:
        if args.live:
            import uvicorn

            from config_api import app

            threading.Thread(
                target=play,
                args=(pool, script, fast, activities),
                kwargs={"once": False},
                name="fake-rig-script",
                daemon=True,
            ).start()

            log.info("Dashboard on http://localhost:%s - Ctrl-C to stop", args.port)

            uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None)

            return 0

        play(pool, script, fast, activities)

        # The manager restarts the crashed agent on its next pass; give it one.
        time.sleep(Config.AGENT_POLL_INTERVAL + 2)

        results, alerts = evaluate(capture, activities, seeded)

        return report(results, alerts, source, fast)

    except KeyboardInterrupt:
        log.info("stopped")
        return 130

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
