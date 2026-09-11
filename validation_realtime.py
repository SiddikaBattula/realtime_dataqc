
"""
Realtime QC checks.

The check logic is identical to the original validate_realtime_data():
SPP, TotalSPM and ROP each keep their own baseline + timestamp and are
written out as separate blocks. Alert strings are byte-for-byte the same.

Only two things changed:
  * state lives on the instance instead of module globals (so it can be
    reset when the source table is switched, and unit-tested)
  * parameter lookup goes through ColumnMapper's logical names
"""

from config import Config
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from logger import get_logger
from column_mapper import ColumnMapper


log = get_logger(__name__)
alert_log = get_logger("qc.alerts")

# How often a set of alerts that has not changed is written out again.
LOG_REPEAT_SECONDS = 60

_TIMESTAMP_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _rule_stamps():
    """
    When each rule file was last written.

    Four stat() calls a second is nothing, and it means the agent needs no
    connection to the config API - the files are the only thing they share.
    """
    files = {
        "ranges": Config.RANGES_FILE,
        "activity": Config.ACTIVITY_FILE,
        "conditions": Config.CONDITIONS_FILE,
        "column_mapping": Config.COLUMN_MAP_FILE,
    }

    stamps = {}

    for name, path in files.items():
        try:
            stamps[name] = Path(path).stat().st_mtime_ns
        except OSError:
            stamps[name] = None

    return stamps


def _alert_fingerprint(alert):
    """
    What makes two alerts "the same alert".

    The text carries a timestamp and the reading that tripped it, and both
    move every second while the problem behind them does not - Co2 at
    11.148% and Co2 at 11.352% are one alert, not two. Taking those out
    leaves the shape of the message, which is what identifies it.
    """
    return _NUMBER.sub("#", _TIMESTAMP_PREFIX.sub("", alert))


def load_json(path, label):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{label} file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} file {path} is not valid JSON: {exc}") from exc


@dataclass
class ValidationResult:
    alerts: list = field(default_factory=list)
    spp_percentage: float = 0.0
    totalspm_percentage: float = 0.0
    rop_percentage: float = 0.0
    activity: str = None
    normalized: dict = field(default_factory=dict)

    def as_tuple(self):
        """Original return signature."""
        return (
            self.alerts,
            self.spp_percentage,
            self.totalspm_percentage,
            self.rop_percentage,
        )


class RealtimeValidator:
    def __init__(self, mapper, ranges, activity_rules, conditions, drilling_criteria):
        self.mapper = mapper
        self.ranges = ranges
        self.activity_rules = activity_rules
        self.conditions = conditions
        self.drilling_criteria = drilling_criteria

        self._apply_conditions()

        # ---- persistent state (was module-level globals) ----
        self.ta_gt_tg_start = None

        self.previous_spp = None
        self.previous_spp_time = None

        self.previous_totalspm = None
        self.previous_totalspm_time = None

        self.previous_rop = None
        self.previous_rop_time = None

        # Hookload monitoring
        self.previous_hookload = None
        self.previous_hookload_time = None


        # ---- what was last written to the log, and when ----
        self._last_fingerprint = None
        self._unchanged_since = None
        self._last_logged = None
        self._last_activity = None

        # What the rule files looked like when they were last read in.
        self._rule_stamps = _rule_stamps()

        self._audit_config()

    # ------------------------------------------------------------------
    # Rule files
    # ------------------------------------------------------------------
    def _apply_conditions(self):
        """Read the thresholds out of conditions.json into plain attributes."""
        conditions = self.conditions

        self.ta_tg_duration = conditions["TA_TG"]["duration_seconds"]

        self.spp_threshold = conditions["SPP"]["percentage_change"]
        self.spp_duration = conditions["SPP"]["duration_seconds"]

        self.totalspm_threshold = conditions["SPM"]["percentage_change"]
        self.totalspm_duration = conditions["SPM"]["duration_seconds"]

        self.rop_threshold = conditions["ROP"]["percentage_change"]
        self.rop_duration = conditions["ROP"]["duration_seconds"]

        self.hookload_duration = conditions["HOOKLOAD"]["duration_seconds"]

    def reload_rules_if_changed(self, row):
        """
        Pick up an edit to data/*.json without a restart.

        The config API writes the files; this notices the change on the next
        reading and reads them in again. The SPP, TotalSPM and ROP baselines
        are left alone, so a threshold change does not throw away the history
        those checks are in the middle of measuring against.

        A file that is unreadable (edited by hand into something invalid) is
        reported once and the rules already in memory keep running.
        """
        stamps = _rule_stamps()

        if stamps == self._rule_stamps:
            return False

        changed = [
            name for name, stamp in stamps.items()
            if self._rule_stamps.get(name) != stamp
        ]

        # Recorded before the reload is attempted: a file that fails to load is
        # not retried until it changes again, so one bad edit cannot fill the
        # log at a line a second.
        self._rule_stamps = stamps

        try:
            if "column_mapping" in changed:
                mapper = ColumnMapper.from_file(Config.COLUMN_MAP_FILE)
                mapper.resolve(list(row.keys()))
                self.mapper = mapper

            if "ranges" in changed:
                self.ranges = load_json(Config.RANGES_FILE, "ranges")

            if "activity" in changed:
                self.activity_rules = load_json(Config.ACTIVITY_FILE, "activity")

            if "conditions" in changed:
                self.conditions = load_json(Config.CONDITIONS_FILE, "conditions")
                self._apply_conditions()

        except Exception as exc:
            log.error(
                "%s changed but could not be loaded (%s) - carrying on with the "
                "rules already in memory",
                ", ".join(changed), exc,
            )
            return False

        log.info("Reloaded after a change to %s", ", ".join(f"{n}.json" for n in changed))

        self._audit_config()

        return True

    # ------------------------------------------------------------------
    # Startup sanity: every param used in the rule files must be mappable
    # ------------------------------------------------------------------
    def _audit_config(self):
        known = set(self.mapper.mapping)
        problems = 0

        def check(param, source):
            nonlocal problems
            if param not in known:
                log.error(
                    "%s refers to '%s' which has no entry in column_mapping.json - "
                    "this check can never run", source, param,
                )
                problems += 1
            elif not self.mapper.is_available(param):
                log.warning(
                    "%s refers to '%s' but no such column exists in the table - "
                    "check skipped", source, param,
                )

        for param in self.ranges:
            check(param, "ranges.json")

        for activity, rules in self.activity_rules.items():
            for param in rules:
                check(param, f"activity.json[{activity}]")

        if problems:
            log.error("%d rule parameter(s) are not defined in column_mapping.json", problems)
        else:
            log.info("Rule files audited: all parameters are present in column_mapping.json")

    # ------------------------------------------------------------------
    def detect_activity(self, data, raise_alert, date_str):

        total_depth = data.get("DEPTH")
        bit_depth = data.get("BIT_DPT_MD")

        # Before the subtraction: either one being missing used to raise
        # TypeError here, and the agent reported it as a loop error.
        if total_depth is None or bit_depth is None:
            raise_alert(
                f"[{date_str}] Cannot determine activity: DEPTH={total_depth}, "
                f"BIT_DPT_MD={bit_depth}",
                "DEPTH", "BIT_DPT_MD",
            )
            return None

        gap = total_depth - bit_depth
        activity = "DRILLING" if gap <= self.drilling_criteria else "RIH"

        # Only when it changes. At one row a second this line was most of the
        # debug log, and every copy of it said the same thing.
        if activity != self._last_activity:
            log.debug(
                "Activity %s (hole %s - bit %s = %.2f m)",
                activity, total_depth, bit_depth, gap,
            )
            self._last_activity = activity

        return activity

    # ------------------------------------------------------------------
    # Main entry point - mirrors the original validate_realtime_data()
    # ------------------------------------------------------------------
    def validate(self, row):
        self.reload_rules_if_changed(row)

        normalized_data, row_errors = self.mapper.normalize(row)
        for err in row_errors:
            log.warning("Row normalisation: %s", err)

        alerts = []

        # The logical names each alert was read from, in step with `alerts`, so
        # the log can say which column of this table actually tripped it.
        sources = []

        def raise_alert(message, *params):
            alerts.append(message)
            sources.append(params)

        date_str = datetime.now().strftime("%d-%m-%y %H-%M-%S")
        depth = normalized_data.get("BIT_DPT_MD")

        spp_percentage = 0.0
        totalspm_percentage = 0.0
        rop_percentage = 0.0

        # ------------------------------------------------------------------
        # 1. Activity conditions
        # ------------------------------------------------------------------
        activity = self.detect_activity(normalized_data, raise_alert, date_str)

        if activity:
            rules = self.activity_rules.get(activity)

            if not rules:
                raise_alert(f"Unknown activity: {activity}")
                log.error("activity.json has no rule block for '%s'", activity)

            else:
                for param, is_mandatory in rules.items():

                    # Rule = 1 means value must be > 0
                    if is_mandatory == 1:

                        if not self.mapper.is_available(param):
                            continue  # column absent from this table, warned at startup

                        value = normalized_data.get(param)

                        if value is None or value <= 0:
                            raise_alert(
                                f"[{date_str}] {param} cannot be 0 in {activity} where BD is:{depth}",
                                param,
                            )
                            

        # ------------------------------------------------------------------
        # 2. Ranges
        # ------------------------------------------------------------------
        for param, limits in self.ranges.items():
            value = normalized_data.get(param)

            if value is None:
                continue

            min_val = limits["min"]
            max_val = limits["max"]
            unit = limits.get("unit", "")

            if value < min_val:
                raise_alert(
                    f"[{date_str}] {param} - {value} {unit} is below minimum limit {min_val}{unit} BD-{depth}",
                    param,
                )

            elif value > max_val:
                raise_alert(
                    f"[{date_str}] {param} - {value} {unit} is above maximum limit {max_val}{unit}  BD-{depth}",
                    param,
                )

        # ------------------------------------------------------------------
        # 3. TA > TG
        # ------------------------------------------------------------------
        ta = normalized_data.get("TA")
        tg = normalized_data.get("TG")

        if ta is not None and tg is not None:
            if ta > tg:
                if self.ta_gt_tg_start is None:
                    self.ta_gt_tg_start = datetime.now()
                    log.debug("TA>TG started (TA=%s TG=%s)", ta, tg)

                elapsed = (datetime.now() - self.ta_gt_tg_start).total_seconds()

                if elapsed >= self.ta_tg_duration:
                    raise_alert(
                        f"[{date_str}] TA is greater than TG where BD-{depth}",
                        "TA", "TG",
                    )
            else:
                # Reset timer when condition clears
                if self.ta_gt_tg_start is not None:
                    log.debug("TA>TG cleared")
                self.ta_gt_tg_start = None

        # ------------------------------------------------------------------
        # 4. SPP change
        # ------------------------------------------------------------------
        spp = normalized_data.get("SPP")

        if spp is not None and spp > 0:
            current_time = datetime.now()

            # First value
            if self.previous_spp is None:
                self.previous_spp = spp
                self.previous_spp_time = current_time

            # Prevent division by zero
            elif self.previous_spp <= 0:
                self.previous_spp = spp
                self.previous_spp_time = current_time

            else:
                elapsed = (current_time - self.previous_spp_time).total_seconds()

                if elapsed >= self.spp_duration:

                    percent_change = ((spp - self.previous_spp) / self.previous_spp) * 100

                    log.debug("SPP %s -> %s over %.1fs = %.2f%%",
                              self.previous_spp, spp, elapsed, percent_change)

                    if percent_change > self.spp_threshold:
                        raise_alert(
                            f"[{date_str}] SPP increased by {percent_change:.2f}% where BD-{depth}",
                            "SPP",
                        )

                    elif percent_change < -self.spp_threshold:
                        raise_alert(
                            f"[{date_str}] SPP dropped by {abs(percent_change):.2f}% where BD-{depth}",
                            "SPP",
                        )

                    # Reset baseline
                    self.previous_spp = spp
                    self.previous_spp_time = current_time

                    spp_percentage = round(percent_change, 2)

        # ------------------------------------------------------------------
        # 5. TotalSPM change
        # ------------------------------------------------------------------
        totalspm = normalized_data.get("SPM")

        if totalspm is not None:
            current_time = datetime.now()

            # First value
            if self.previous_totalspm is None:
                self.previous_totalspm = totalspm
                self.previous_totalspm_time = current_time

            # Prevent division by zero
            elif self.previous_totalspm <= 0:
                self.previous_totalspm = totalspm
                self.previous_totalspm_time = current_time

            else:
                elapsed = (current_time - self.previous_totalspm_time).total_seconds()

                if elapsed >= self.totalspm_duration:

                    percent_change = (
                        (totalspm - self.previous_totalspm) / self.previous_totalspm
                    ) * 100

                    log.debug("TotalSPM %s -> %s over %.1fs = %.2f%%",
                              self.previous_totalspm, totalspm, elapsed, percent_change)

                    if percent_change > self.totalspm_threshold:
                        raise_alert(
                            f"[{date_str}] TotalSPM increased by {percent_change:.2f}% Where BD-{depth}",
                            "SPM",
                        )

                    elif percent_change < -self.totalspm_threshold:
                        raise_alert(
                            f"[{date_str}] TotalSPM dropped by {abs(percent_change):.2f}% Where BD-{depth}",
                            "SPM",
                        )

                    self.previous_totalspm = totalspm
                    self.previous_totalspm_time = current_time

                    totalspm_percentage = round(percent_change, 2)

        # ------------------------------------------------------------------
        # 6. ROP change
        # ------------------------------------------------------------------
        rop = normalized_data.get("ROP")

        if rop is not None:
            current_time = datetime.now()

            # First value
            if self.previous_rop is None:
                self.previous_rop = rop
                self.previous_rop_time = current_time

            # Prevent division by zero. ROP is legitimately 0 whenever the bit is
            # not advancing (tripping, connections, circulating), so without this
            # the next reading would divide by a zero baseline.
            elif self.previous_rop <= 0:
                self.previous_rop = rop
                self.previous_rop_time = current_time

            else:
                elapsed = (current_time - self.previous_rop_time).total_seconds()

                if elapsed >= self.rop_duration:

                    percent_change = ((rop - self.previous_rop) / self.previous_rop) * 100

                    log.debug("ROP %s -> %s over %.1fs = %.2f%%",
                              self.previous_rop, rop, elapsed, percent_change)

                    if percent_change > self.rop_threshold:
                        raise_alert(
                            f"[{date_str}] ROP increased by {percent_change:.2f}% Where BD-{depth}",
                            "ROP",
                        )

                    self.previous_rop = rop
                    self.previous_rop_time = current_time

                    rop_percentage = round(percent_change, 2)
        self._log_reading(
            activity, depth, ta, tg,
            spp_percentage, totalspm_percentage, rop_percentage,
            alerts, sources,
        )

        return ValidationResult(
            alerts=alerts,
            spp_percentage=spp_percentage,
            totalspm_percentage=totalspm_percentage,
            rop_percentage=rop_percentage,
            activity=activity,
            normalized=normalized_data,
        )

        # ------------------------------------------------------------------
        # 7. Hookload unchanged
        # ------------------------------------------------------------------
        hookload = normalized_data.get("HOOKLOAD")

        if hookload is not None:

            current_time = datetime.now()

            # First reading
            if self.previous_hookload is None:
                self.previous_hookload = hookload
                self.previous_hookload_time = current_time

            else:
                elapsed = (
                    current_time - self.previous_hookload_time
                ).total_seconds()

                # Hookload stayed exactly the same
                if hookload == self.previous_hookload:

                    if elapsed >= self.hookload_duration:
                        raise_alert(
                            f"[{date_str}] Please check for data TS. HOOKLOAD has remained unchanged for {int(elapsed)} seconds",
                        )

                        # Reset timer so alert doesn't fire every second
                        self.previous_hookload_time = current_time

                else:
                    # Value changed -> start monitoring again
                    self.previous_hookload = hookload
                    self.previous_hookload_time = current_time

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _columns_note(self, params):
        """
        "TG = Total_Gas" for each logical name a check read.

        The rules are written against logical names, so an alert about TG says
        nothing about where to look in the table. This is the answer to "which
        column is that?" without opening column_mapping.json.
        """
        named = []

        for param in params:
            column = self.mapper.column_for(param)
            named.append(f"{param} = {column}" if column else f"{param} = not in table")

        return ", ".join(named)

    def _log_reading(self, activity, depth, ta, tg,
                     spp_percentage, totalspm_percentage, rop_percentage,
                     alerts, sources):
        """
        One record per reading, never one per alert - and not once a second.

        Rows arrive about every second and mostly repeat what the last one
        said, so the full block is written when what is wrong actually changes:
        an alert appears, one clears, or the set of them is different. While
        the same alerts stay up it is repeated as a single line every
        LOG_REPEAT_SECONDS, so the log says the problem is still there without
        burying everything else. Every reading is still logged in full at DEBUG.

        Alerts continue to be saved to the database on every reading - this
        changes what is written to the log, not what is recorded.
        """
        now = datetime.now()

        summary = (
            f"{activity} | depth {depth} | TA {ta} TG {tg} | "
            f"change SPP {spp_percentage}% SPM {totalspm_percentage}% "
            f"ROP {rop_percentage}%"
        )

        alert_log.debug("%s | %d alert(s)", summary, len(alerts))

        fingerprint = tuple(_alert_fingerprint(alert) for alert in alerts)

        if fingerprint != self._last_fingerprint:

            self._last_fingerprint = fingerprint
            self._unchanged_since = now
            self._last_logged = now

            if not alerts:
                alert_log.info("%s | all clear", summary)
                return

            lines = []

            for number, alert in enumerate(alerts, 1):
                # The alert text carries its own timestamp for the database
                # row; this record already has one, so it comes off here.
                message = _TIMESTAMP_PREFIX.sub("", alert)
                note = self._columns_note(
                    sources[number - 1] if number <= len(sources) else ()
                )

                lines.append(
                    f"  {number}. {message}   [{note}]" if note
                    else f"  {number}. {message}"
                )

            listed = "\n".join(lines)

            alert_log.warning(
                "\n"
                f"Activity     : {activity}\n"
                f"Depth        : {depth}\n"
                f"TA           : {ta}\n"
                f"TG           : {tg}\n"
                f"SPP % change : {spp_percentage}\n"
                f"SPM % change : {totalspm_percentage}\n"
                f"ROP % change : {rop_percentage}\n"
                f"Alerts ({len(alerts)})   :\n"
                f"{listed}\n"
            )
            return

        # Same as the last reading - say so occasionally, not every second.
        if (now - self._last_logged).total_seconds() < LOG_REPEAT_SECONDS:
            return

        self._last_logged = now
        held = int((now - self._unchanged_since).total_seconds())

        if alerts:
            alert_log.warning(
                "%s | the same %d alert(s) have been up for %ds",
                summary, len(alerts), held,
            )
        else:
            alert_log.info("%s | still clear after %ds", summary, held)

    # ------------------------------------------------------------------
    def validate_realtime_data(self, data: dict):
        """
        Original signature.
        Returns: (alerts, spp_percentage, totalspm_percentage, rop_percentage)
        """
        return self.validate(data).as_tuple()

    def reset_state(self):
        """Clear timers/baselines - use when the source table is switched."""
        self.ta_gt_tg_start = None
        self.previous_spp = self.previous_spp_time = None
        self.previous_totalspm = self.previous_totalspm_time = None
        self.previous_rop = self.previous_rop_time = None
        self._last_fingerprint = None
        self._unchanged_since = self._last_logged = None
        self._last_activity = None
        log.info("Validator state reset")


# ----------------------------------------------------------------------
# Backwards-compatible module-level entry point.
#
# Lets older code keep doing:
#     from validation_realtime import validate_realtime_data
#     alerts, spp_pct, spm_pct, rop_pct = validate_realtime_data(row)
#
# The validator is built lazily on the first row and the column mapping is
# resolved against that row's keys (SELECT * gives every column of the table).
# Prefer building RealtimeValidator yourself in the agent: that resolves the
# mapping at startup, so a missing critical column fails immediately instead
# of on the first row.
# ----------------------------------------------------------------------

_VALIDATOR = None


def get_validator(sample_row=None):
    """Return the process-wide validator, building it on first use."""
    global _VALIDATOR

    if _VALIDATOR is None:
        if not sample_row:
            raise RuntimeError(
                "get_validator() needs a sample row on first call so the column "
                "mapping can be resolved against the table"
            )

        mapper = ColumnMapper.from_file(
            Config.COLUMN_MAP_FILE
        )
        mapper.resolve(list(sample_row.keys()))

        _VALIDATOR = RealtimeValidator(
            mapper=mapper,
            ranges=load_json(Config.RANGES_FILE, "ranges"),
            activity_rules=load_json(Config.ACTIVITY_FILE, "activity"),
            conditions=load_json(Config.CONDITIONS_FILE, "conditions"),
            drilling_criteria=Config.DRILLING_CRITERIA,
        )
        log.info("Validator initialised from first row (%d columns)", len(sample_row))

    return _VALIDATOR


def validate_realtime_data(data: dict):
    """
    Original signature.
    Returns: (alerts, spp_percentage, totalspm_percentage, rop_percentage)
    """
    return get_validator(data).validate(data).as_tuple()


def reset_validator():
    """Drop the singleton - call this if the source table changes at runtime."""
    global _VALIDATOR
    _VALIDATOR = None



    

def build_validator(sample_row):

    mapper = ColumnMapper.from_file(
        Config.COLUMN_MAP_FILE
    )

    mapper.resolve(
        list(sample_row.keys())
    )

    return RealtimeValidator(
        mapper=mapper,
        ranges=load_json(
            Config.RANGES_FILE,
            "ranges"
        ),
        activity_rules=load_json(
            Config.ACTIVITY_FILE,
            "activity"
        ),
        conditions=load_json(
            Config.CONDITIONS_FILE,
            "conditions"
        ),
        drilling_criteria=Config.DRILLING_CRITERIA,
    )