"""
Realtime QC checks - the rules that turn one reading into a list of alerts.

One RealtimeValidator per well. It holds the state the change checks need
(the previous SPP, SPM, ROP and HOOKLOAD readings and when each was taken),
so it cannot be shared between wells - build one per agent.

Seven checks run on every reading, in this order:

  1. activity      DRILLING or NON DRILLING, from hole depth minus bit depth
                   against this well's drilling_criteria, then every parameter
                   its activity block marks 1 must be above 0
  2. ranges        each parameter inside its min/max from its ranges block,
                   after `factor` converts the reading into the limits' unit -
                   multiplying, or dividing for ROP (see INVERSE_PARAMS)
  3. TA > TG       alert once TA has been above TG for TA_TG.duration_seconds
  4. SPP           percentage move over SPP.duration_seconds, either direction
  5. SPM           the same, for total pump strokes per minute
  6. ROP           the same, but an increase only - a drop to zero is normal
                   whenever the bit comes off bottom. Measured on the reading
                   after `factor`, so a rise means the bit is drilling faster
                   and not that the raw minutes-per-metre column went up
  7. HOOKLOAD      alert when the value has not moved at all for
                   HOOKLOAD.duration_seconds, which means a stalled feed

Rules come from the well's own file in data/wells/ and are re-read when it
changes, without a restart - so two rigs can disagree about their column
names, their limits and how far off bottom still counts as drilling.
Parameters are referred to by logical name throughout (SPP, ROP, HOOKLOAD
...); ColumnMapper is what turns those into this table's columns.
"""

import re

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import rule_files
import well_rules
from alert_log import AlertLog
from column_mapper import ColumnMapper, to_number
from config import Config
from logger import get_logger
from rule_files import DRILLING, NON_DRILLING, RuleFileError


log = get_logger(__name__)

# The timestamp every alert starts with, "[17-09-26 05-32-43]". The alerts
# endpoint and the agent read it back to tell how old an alert is.
ALERT_TIME_FORMAT = "%d-%m-%y %H-%M-%S"

_ALERT_STAMP = re.compile(r"^\[([^\]]*)\]")

# Subjects raised by checks that only look once per window (or, for HOOKLOAD,
# once per stall period). A reading in between says nothing about them, so
# they are not cleared just for not being raised - their own check clears them
# when it looks and finds nothing.
EVENT_SUBJECTS = {"SPP_CHANGE", "SPM_CHANGE", "ROP_CHANGE", "HOOKLOAD_STUCK"}

# Parameters whose column holds the reciprocal of the unit their limits are
# written in, so `factor` divides the reading instead of multiplying it.
#
# ROP is the only one: the rig stores minutes per metre and ranges is written
# in metres per hour, and 60 / 0.5 min/m = 120 m/hr. Every other parameter is
# a straight multiply - HOOKLOAD's 2.268 turns daN into klbf.
INVERSE_PARAMS = {"ROP"}

# The pump columns SPM is totalled from. A rig with three pumps simply has no
# column for the other two, and they are skipped.
PUMPS = ("MP1_SPM", "MP2_SPM", "MP3_SPM", "MP4_SPM", "MP5_SPM")

# Logical names that are worked out from other columns instead of being read
# from one of their own.
#
# SPM is the only one: wherever it is checked - the range check and the
# activity zero-check both - the value used is PUMPS added up, never whatever
# a column called SPM holds. So a rig whose table has no SPM column (this one
# calls its own total TOT_SPM) is checked perfectly well, and "no such column
# exists, check skipped" is wrong about it twice over. What actually matters is
# whether the pumps resolved, which is what _audit_config asks instead.
DERIVED_PARAMS = {"SPM"}

# Absent columns that are not a mistake. A four-pump rig has no MP5_SPM, and
# _get_total_spm adds up whichever pumps are there - so telling the reader to
# go and put the right column name in is advice about a pump that does not
# exist.
OPTIONAL_PARAMS = set(PUMPS)

# Logical names that are worked out from other columns instead of being read
# from one of their own.
#
# SPM is the only one: wherever it is checked - the range check and the
# activity zero-check both - the value used is PUMPS added up, not whatever a
# column called SPM holds. So a rig whose table has no SPM column (this one
# calls its total TOT_SPM) is perfectly well checked, and saying "no such
# column exists, check skipped" about it is wrong twice over. What actually
# matters is whether the pumps resolved, which is what _audit_config asks.
DERIVED_PARAMS = {"SPM"}

def _stamp(path):
    """
    When a well's rule file was last written.

    One stat() a second is nothing, and it means the agent needs no connection
    to the config API - the file is the only thing they share.
    """
    if path is None:
        return None

    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return None


def alert_raised_at(alert):
    """When an alert was raised, from its "[17-09-26 05-32-43]" prefix, or None."""
    match = _ALERT_STAMP.match(alert) if isinstance(alert, str) else None

    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), ALERT_TIME_FORMAT)
    except ValueError:
        return None


@dataclass
class ValidationResult:
    alerts: list = field(default_factory=list)

    # Everything wrong on this reading, repeats included. `alerts` is only what
    # was new, so a well with a standing problem reports 0 new alerts a second
    # later - which reads as "nothing wrong" unless this is there too.
    standing: int = 0

    spp_percentage: float = 0.0
    totalspm_percentage: float = 0.0
    rop_percentage: float = 0.0
    activity: str = None
    normalized: dict = field(default_factory=dict)


class RealtimeValidator:
    def __init__(self, mapper, ranges, activity_rules, conditions, drilling_criteria,bit_depth_threshold,
                 rules_path=None, well=None):
        # One process runs an agent per well and they all write to the same
        # files, so the well's name goes in the logger rather than being left
        # for each message to remember: every line is then attributable, and
        # `grep "qc.alerts.KJ-16" logs/app.log` is one well's whole story.
        self.well = well or "unknown-well"
        self.log = get_logger(f"validation.{self.well}")

        # Everything about what the log says, and why, lives in alert_log.py -
        # this class is the drilling rules. refresh() below hands it the rules
        # in force so its sentences quote the limits that are actually running.
        self.alert_log = AlertLog(self.well)

        self.mapper = mapper
        self.ranges = ranges
        self.activity_rules = activity_rules
        self.conditions = conditions

        # Metres of hole depth minus bit depth that still count as on bottom.
        # This well's own - build_validator reads it out of its rules, and
        # reload_rules_if_changed picks up an edit to it within a reading.
        self.drilling_criteria = drilling_criteria

        # subject -> what its last saved alert said (see raise_alert in
        # validate). A subject is dropped from here when its problem clears.
        self._last_alerted = {}


        # The well file these rules came from, watched for edits. None means
        # nothing to watch - the rules were handed in directly.
        self.rules_path = rules_path

        self._apply_conditions()

        # ---- persistent state (was module-level globals) ----
        self.ta_gt_tg_start = None

        # self.previous_spp = None
        # self.previous_spp_time = None

        self.spp_spm_factor = 0.0

# Factor calculation timer
        self.spp_spm_factor_time = datetime.now()

        # Comparison timer
        self.spp_comparison_time = datetime.now()

        # Factor refresh interval
        self.spp_factor_duration = 60.0

        self.previous_rop = None
        self.previous_rop_time = None

        # Hookload monitoring
        self.previous_hookload = None
        self.previous_hookload_time = None

        self.hookload_alerted = False


        # What the last reading was called, so a change of activity is logged
        # once rather than every second. What was last written to the alert log
        # is the alert log's own business - see AlertLog.
        self._last_activity = None

        self.last_bit_depth = None
        self.bit_depth_threshold = bit_depth_threshold

        # What the well's rule file looked like when it was last read in.
        self._rule_stamp = _stamp(self.rules_path)

        self._refresh_alert_log()

        self._audit_config()

        # What each parameter is called in alert text, from display_name.json.
        # The stamp starts as something no file can match, so the first call
        # always reads it.
        self.display_names = {}
        self._display_stamp = object()
        self.reload_display_names_if_changed()

    # ------------------------------------------------------------------
    # Rule files
    # ------------------------------------------------------------------
    def _apply_conditions(self):
        """Read the thresholds out of conditions.json into plain attributes."""
        conditions = self.conditions

        self.ta_tg_duration = conditions["TA_TG"]["duration_seconds"]

        self.spp_threshold = conditions["SPP"]["percentage_change"]
        self.spp_duration = conditions["SPP"]["duration_seconds"]

        # self.totalspm_threshold = conditions["SPM"]["percentage_change"]
        # self.totalspm_duration = conditions["SPM"]["duration_seconds"]

        self.rop_threshold = conditions["ROP"]["percentage_change"]
        self.rop_duration = conditions["ROP"]["duration_seconds"]

        self.hookload_duration = conditions["HOOKLOAD"]["duration_seconds"]

    def reload_rules_if_changed(self, row):
        """
        Pick up an edit to this well's rules without a restart.

        The dashboard writes the well's file; this notices on the next reading
        and reads it in again. The SPP, TotalSPM, ROP and HOOKLOAD baselines
        are left alone, so changing a threshold does not throw away the
        history those checks are in the middle of measuring against.

        A file that cannot be read is reported once and the rules already in
        memory keep running - a well should not stop being checked because
        someone saved something odd.
        """
        if self.rules_path is None:
            return False

        stamp = _stamp(self.rules_path)

        if stamp == self._rule_stamp:
            return False

        # Recorded before the reload is attempted: a file that fails to load
        # is not retried until it changes again, so one bad save cannot fill
        # the log at a line a second.
        self._rule_stamp = stamp

        try:
            rules = well_rules.load_rules(self.rules_path)

            mapper = ColumnMapper.from_mapping(
                rules["column_mapping"],
                derived=DERIVED_PARAMS,
                optional=OPTIONAL_PARAMS,
            )
            mapper.resolve(list(row.keys()))

            self.mapper = mapper
            self.ranges = rules["ranges"]
            self.activity_rules = rules["activity"]
            self.conditions = rules["conditions"]
            self.drilling_criteria = rule_files.drilling_criteria_of(rules)
            self.bit_depth_threshold = rules.get("bit_depth_threshold",rule_files.DEFAULT_BIT_DEPTH_THRESHOLD)

            self._apply_conditions()
            self._refresh_alert_log()

        except Exception as exc:
            self.log.error(
                "%s changed but could not be loaded (%s) - carrying on with "
                "the rules already in memory",
                Path(self.rules_path).name, exc,
            )
            return False

        self.log.info("Reloaded rules from %s", Path(self.rules_path).name)

        self._audit_config()

        return True

    def reload_display_names_if_changed(self):
        """
        Pick up an edit to display_name.json - from Settings or by hand - on
        the next reading, without a restart.

        The names are shared by every well, so each well's validator watches
        the same file. One that cannot be read is reported once and the names
        already in memory stay, the same as with the rules.
        """
        stamp = _stamp(Config.DISPLAY_NAME_FILE)

        if stamp == self._display_stamp:
            return

        self._display_stamp = stamp

        try:
            names = rule_files.load("display_name")

        except RuleFileError as exc:
            self.log.error(
                "display_name.json could not be loaded (%s) - alerts keep the "
                "names already in memory", exc,
            )
            return

        if not isinstance(names, dict):
            self.log.error(
                "display_name.json must map parameters to names - alerts keep "
                "the names already in memory"
            )
            return

        # A blank or non-text name would print as nothing, so the parameter
        # keeps its own name instead.
        self.display_names = {
            param: name.strip()
            for param, name in names.items()
            if isinstance(name, str) and name.strip()
        }

        self.log.info("Display names loaded: %d", len(self.display_names))

    # ------------------------------------------------------------------
    # Startup sanity: every param used in the rule files must be mappable
    # ------------------------------------------------------------------
    def _audit_config(self):
        known = set(self.mapper.mapping)
        problems = 0

        def check(param, source):
            nonlocal problems

            if param not in known:
                self.log.error(
                    "%s refers to '%s' which has no entry in column_mapping - "
                    "this check can never run", source, param,
                )
                problems += 1
                return

            if param in DERIVED_PARAMS:
                # Worked out, not read. The table having no column of this name
                # is normal; what would break the check is having nothing left
                # to work it out from.
                if not self.derived_from(param):
                    self.log.error(
                        "%s refers to '%s', which is worked out from %s - none of "
                        "those columns exist in the table either, so it is 0 on "
                        "every reading", source, param, ", ".join(PUMPS),
                    )
                    problems += 1

                return

            if not self.mapper.is_available(param):
                self.log.warning(
                    "%s refers to '%s' but no such column exists in the table - "
                    "check skipped", source, param,
                )

        for param in self.ranges:
            check(param, "ranges")

        for activity, rules in self.activity_rules.items():
            for param in rules:
                check(param, f"activity[{activity}]")

        # Said once, not per rule that mentions it: three identical lines about
        # SPM were most of what this audit used to print.
        for param in sorted(DERIVED_PARAMS):
            columns = self.derived_from(param)

            if param in known and columns:
                self.log.info(
                    "%s is not read from a column of its own - it is %s added up",
                    param, " + ".join(columns),
                )

        if problems:
            self.log.error(
                "%d rule parameter(s) cannot be checked against this table", problems
            )
        else:
            self.log.info("Rules audited: every parameter can be checked")

    def derived_from(self, param):
        """
        The columns `param` is worked out from, of the ones this table has.

        Empty means it cannot be worked out at all. A four-pump rig returns
        four of the five, which is not a problem - _get_total_spm adds up
        whatever is there.
        """
        if param == "SPM":
            return [pump for pump in PUMPS if self.mapper.is_available(pump)]

        return []

    # ------------------------------------------------------------------
    def _apply_factor(self, param, value, factor):
        """
        The reading converted into the unit its limits are written in.

        `factor` is the number entered against the parameter in the form. For
        almost everything it multiplies: HOOKLOAD's 2.268 turns the stored daN
        into the klbf its limits are written in. For a parameter in
        INVERSE_PARAMS it divides instead, because the column holds the
        reciprocal unit - ROP's 60 turns 0.5 minutes per metre into 120 metres
        per hour. Leave the factor out and the column is compared as stored,
        ROP included.

        Returns None when the reading cannot be converted at all - a ROP of 0
        is the bit not advancing, and 60/0 is not a speed. The caller skips the
        check rather than comparing a number that means nothing.

        A factor that is not a usable number is reported and ignored rather
        than allowed to skip the check.
        """
        if factor is None:
            return value

        multiplier = to_number(factor)

        if multiplier is None or multiplier == 0:
            self.log.error(
                "ranges: '%s' has factor %r, which is not a usable number - "
                "comparing the raw value instead",
                param, factor,
            )
            return value

        if param.upper() in INVERSE_PARAMS:
            if value == 0:
                return None

            return round(multiplier / value, 4)

        return round(value * multiplier, 4)

    def _in_limit_unit(self, param, value):
        """
        `value` in the unit this well's limits for `param` are written in.

        The one place a reading is converted, so the range check and the
        percentage-change checks cannot end up comparing different units - see
        the ROP change check, which was reading the raw column while the range
        check beside it read m/hr.
        """
        if value is None:
            return None

        return self._apply_factor(
            param, value, self.ranges.get(param, {}).get("factor")
        )

    def _refresh_alert_log(self):
        """Tell the log which rules are in force, at build and at every reload."""
        self.alert_log.refresh(self.mapper, self.ranges, self.drilling_criteria)

    def _get_total_spm(self, normalized_data):
        total_spm = 0

        for pump in PUMPS:
            value = normalized_data.get(pump)

            if value is not None:
                total_spm += value

        return total_spm


    # ------------------------------------------------------------------
    def detect_activity(self, data, raise_alert, date_str):
        """
        What the rig is doing, from how far the bit is off bottom.

        The margin is this well's own drilling_criteria, not a figure shared
        by every rig: one rig's depth channels agree to the centimetre and
        another's are half a metre apart while still on bottom, and reading
        the second one against the first's margin would call every reading
        NON DRILLING and run the wrong set of activity checks all shift.
        """
        total_depth = data.get("DEPTH")
        bit_depth = data.get("BIT_DPT_MD")

        if total_depth is None or bit_depth is None:
            raise_alert(
                f"[{date_str}] Cannot determine activity: "
                f"{self.display_name('DEPTH')}={total_depth}, "
                f"{self.display_name('BIT_DPT_MD')}={bit_depth}",
                "DEPTH", "BIT_DPT_MD",
                subject="ACTIVITY_UNDETERMINED",
                value=(total_depth is None, bit_depth is None),
                why=(
                    f"activity needs both depths: DEPTH={total_depth} "
                    f"(column {self.mapper.column_for('DEPTH')}), "
                    f"BIT_DPT_MD={bit_depth} "
                    f"(column {self.mapper.column_for('BIT_DPT_MD')}) - "
                    "one of them is missing or not a number in this row"
                ),
            )
            return None

        gap = total_depth - bit_depth

        activity = DRILLING if gap <= self.drilling_criteria else NON_DRILLING

        if activity != self._last_activity:
            # The margin is logged with the gap: "why is this well DRILLING at
            # 0.4 m off bottom" is answered by the two numbers together.
            self.log.debug(
                "Activity %s (hole %s - bit %s = %.2f m, criteria %g m)",
                activity, total_depth, bit_depth, gap, self.drilling_criteria,
            )
            self._last_activity = activity

        return activity

    def display_name(self, param):
        """What `param` is called in alert text: its display name, or itself."""
        return self.display_names.get(param, param)
    # ------------------------------------------------------------------
    # Main entry point - mirrors the original validate_realtime_data()
    # ------------------------------------------------------------------
    def validate(self, row):
        self.reload_rules_if_changed(row)
        self.reload_display_names_if_changed()

        normalized_data, row_errors = self.mapper.normalize(row)
        for err in row_errors:
            self.log.warning("Row normalisation: %s", err)

        # New alerts only - what gets saved and shown on the dashboard.
        alerts = []
        sources = []

        # Everything wrong on this reading, repeats included - what the log
        # reports, so a problem that is still there is not logged as cleared.
        standing = []
        standing_sources = []
        standing_reasons = []

        raised = set()

        def raise_alert(message, *params, subject, value=None, why=None):
            """
            Record a problem, unless it repeats the last alert about the same thing.

            `subject` is what the alert is about (ROP_CHANGE, RANGE:HOOKLOAD,
            ZERO:SPP ...). `value` is what it says about it - the part of the
            message that matters, leaving out the timestamp and the bit depth.
            "ROP increased by 12%" at BD 2239 and again at BD 2240 is the same
            alert and is saved once; "ROP increased by 30%" is a new one.

            `why` is the arithmetic behind it, in the units the check actually
            compared: which reading, off which column, against which limit. It
            is for the log only and never reaches the alert file - the alert
            text is what the rig crew reads, this is what answers "why did that
            fire?" at 3am without anyone having to re-derive it from the rules.
            """
            raised.add(subject)
            standing.append(message)
            standing_sources.append(params)
            standing_reasons.append(why)

            if subject in self._last_alerted and self._last_alerted[subject] == value:
                return

            self._last_alerted[subject] = value
            alerts.append(message)
            sources.append(params)

        date_str = datetime.now().strftime(ALERT_TIME_FORMAT)
        bit_depth = normalized_data.get("BIT_DPT_MD")
        total_depth=normalized_data.get("DEPTH")
        depth_unit = self.ranges.get("DEPTH", {}).get("unit", "")
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
                raise_alert(
                    f"[{date_str}] Unknown activity: {activity}",
                    subject="ACTIVITY_UNKNOWN",
                    value=activity,
                    why=(
                        f"the rig is {activity} but this well's activity rules "
                        f"only cover {', '.join(self.activity_rules) or 'nothing'} - "
                        "no zero-checks could be run for this reading"
                    ),
                )
                self.log.error("activity.json has no rule block for '%s'", activity)

            else:
               for param, is_mandatory in rules.items():

                    # Only validate parameters marked as mandatory
                    if is_mandatory != 1:
                        continue

                    if param == "SPM":
                        
                        value = self._get_total_spm(normalized_data)
                        if value <= 0:
                            raise_alert(
                                f"[{date_str}] {self.display_name(param)} cannot be 0 in {activity} where BD:{bit_depth}{depth_unit}, MD:{total_depth}{depth_unit}",
                                "SPM",
                                subject="ZERO:SPM",
                                value=activity,
                                why=(
                                    f"activity[{activity}] requires SPM above 0; "
                                    f"pumps total {value} "
                                    f"({self.alert_log.pump_breakdown(normalized_data, PUMPS)})"
                                ),
                            )

                        continue

                    if not self.mapper.is_available(param):
                        continue

                    value = normalized_data.get(param)

                    if value is None or value <= 0:
                        raise_alert(
                            f"[{date_str}] {self.display_name(param)} cannot be 0 in {activity} where BD:{bit_depth}{depth_unit}, MD:{total_depth}{depth_unit}",
                            param,
                            subject=f"ZERO:{param}",
                            value=activity,
                            why=self.alert_log.zero_reason(param, activity, value),
                        )
                                            

        # ------------------------------------------------------------------
        # 2. Ranges
        # ------------------------------------------------------------------

        for param, limits in self.ranges.items():
            if param == "SPM":
                value = self._get_total_spm(normalized_data)

                if value <= 0:
                    continue
            else:
                value = normalized_data.get(param)

                if value is None:
                    continue

            # min, max and factor are tolerated as strings in the file
            # ("60"), so they are coerced here rather than compared raw -
            # comparing a float against a str is a TypeError, and it would
            # take down the whole reading, not just this one check.
            min_val = to_number(limits.get("min"))
            max_val = to_number(limits.get("max"))

            if min_val is None or max_val is None:
                self.log.error(
                    "ranges.json: '%s' has a min/max that is not a number "
                    "(%r / %r) - range check skipped",
                    param, limits.get("min"), limits.get("max"),
                )
                continue

            # Compared as floats, quoted as they are written in the file, so a
            # limit of 200 still reads "200" in the alert and not "200.0".
            min_text = limits["min"]
            max_text = limits["max"]

            unit = limits.get("unit", "")

            # `factor` converts the stored reading into the unit the limits
            # are written in, before either is compared. ROP is the reason it
            # exists: the table stores it in minutes per metre and the limits
            # are in m/hr. Without this the limits were being applied to the
            # raw column, which is the unit they were never written for.
            raw = value
            value = self._in_limit_unit(param, value)

            if value is None:
                # ROP at 0: the bit is not advancing, which is normal on every
                # connection and trip. There is no speed to range-check, so
                # this reading says nothing about the parameter either way.
                self.log.debug("%s is 0 - nothing to convert, range check skipped", param)
                continue

            if value < min_val:
                raise_alert(
                    f"[{date_str}] {self.display_name(param)} : {value:.4f}{unit} below limit {min_text}{unit} BD : {bit_depth}{depth_unit} ",
                    param,
                    subject=f"RANGE:{param}",
                    value=f"below {value:.2f}",
                    why=self.alert_log.range_reason(
                    param, raw, value, limits, "below", min_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
                )

            elif value > max_val:
                raise_alert(
                    f"[{date_str}] {self.display_name(param)} : {value:.4f}{unit} above limit {max_text}{unit}  BD : {bit_depth}{depth_unit}",
                    param,
                    subject=f"RANGE:{param}",
                    value=f"above {value:.2f}",
                    why=self.alert_log.range_reason(
                    param, raw, value, limits, "above", max_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
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
                    self.log.debug("TA>TG started (TA=%s TG=%s)", ta, tg)

                elapsed = (datetime.now() - self.ta_gt_tg_start).total_seconds()

                if elapsed >= self.ta_tg_duration:
                    raise_alert(
                        f"[{date_str}] {self.display_name('TA')} is greater than "
                        f"{self.display_name('TG')} where BD:{bit_depth}",
                        "TA",
                        "TG",
                        subject="TA_TG",
                        why=(
                            f"TA={ta} has been above TG={tg} for {elapsed:.0f}s, "
                            f"past conditions[TA_TG] of {self.ta_tg_duration}s"
                        ),
                    )
            else:
                # Reset timer when condition clears
                if self.ta_gt_tg_start is not None:
                    self.log.debug("TA>TG cleared")
                self.ta_gt_tg_start = None

        # # ------------------------------------------------------------------
        # # 4. SPP change
        # # ------------------------------------------------------------------
        # spp = normalized_data.get("SPP")

        # if spp is not None and spp > 0:
        #     current_time = datetime.now()

        #     # First value
        #     if self.previous_spp is None:
        #         self.previous_spp = spp
        #         self.previous_spp_time = current_time

        #     # Prevent division by zero
        #     elif self.previous_spp <= 0:
        #         self.previous_spp = spp
        #         self.previous_spp_time = current_time

        #     else:
        #         elapsed = (current_time - self.previous_spp_time).total_seconds()

        #         if elapsed >= self.spp_duration:

        #             percent_change = ((spp - self.previous_spp) / self.previous_spp) * 100

        #             self.log.debug("SPP %s -> %s over %.1fs = %.2f%%",
        #                       self.previous_spp, spp, elapsed, percent_change)

        #             if percent_change > self.spp_threshold:
        #                 raise_alert(
        #                     f"[{date_str}] {self.display_name('SPP')} increased by {percent_change:.2f}% where BD:{bit_depth}{depth_unit}",
        #                     "SPP",
        #                     subject="SPP_CHANGE",
        #                     value=f"increased {percent_change:.2f}",
        #                     why=self.alert_log.change_reason(
        #                         "SPP", self.previous_spp, spp, elapsed,
        #                         percent_change, self.spp_threshold,
        #                         self.spp_duration,
        #                     ),
        #                 )

        #             elif percent_change < -self.spp_threshold:
        #                 raise_alert(
        #                     f"[{date_str}] {self.display_name('SPP')} dropped by {abs(percent_change):.2f}% where BD:{bit_depth}{depth_unit}",
        #                     "SPP",
        #                     subject="SPP_CHANGE",
        #                     value=f"dropped {abs(percent_change):.2f}",
        #                     why=self.alert_log.change_reason(
        #                         "SPP", self.previous_spp, spp, elapsed,
        #                         percent_change, self.spp_threshold,
        #                         self.spp_duration,
        #                     ),
        #                 )

        #             else:
        #                 # Looked and found a steady SPP: the next move is a new
        #                 # alert even if it is the same size as the last one.
        #                 self._last_alerted.pop("SPP_CHANGE", None)

        #             # Reset baseline
        #             self.previous_spp = spp
        #             self.previous_spp_time = current_time

        #             spp_percentage = round(percent_change, 2)

        # # ------------------------------------------------------------------
        # # 5. TotalSPM change
        # # ------------------------------------------------------------------
        # totalspm = normalized_data.get("SPM")

        # if totalspm is not None:
        #     current_time = datetime.now()

        #     # First value
        #     if self.previous_totalspm is None:
        #         self.previous_totalspm = totalspm
        #         self.previous_totalspm_time = current_time

        #     # Prevent division by zero
        #     elif self.previous_totalspm <= 0:
        #         self.previous_totalspm = totalspm
        #         self.previous_totalspm_time = current_time

        #     else:
        #         elapsed = (current_time - self.previous_totalspm_time).total_seconds()

        #         if elapsed >= self.totalspm_duration:

        #             percent_change = (
        #                 (totalspm - self.previous_totalspm) / self.previous_totalspm
        #             ) * 100

        #             self.log.debug("TotalSPM %s -> %s over %.1fs = %.2f%%",
        #                       self.previous_totalspm, totalspm, elapsed, percent_change)

        #             if percent_change > self.totalspm_threshold:
        #                 raise_alert(
        #                     f"[{date_str}] TotalSPM increased by {percent_change:.2f}% Where BD-{depth}",
        #                     "SPM",
        #                 )

        #             elif percent_change < -self.totalspm_threshold:
        #                 raise_alert(
        #                     f"[{date_str}] TotalSPM dropped by {abs(percent_change):.2f}% Where BD-{depth}",
        #                     "SPM",
        #                 )

        #             self.previous_totalspm = totalspm
        #             self.previous_totalspm_time = current_time

        #             totalspm_percentage = round(percent_change, 2)



        # ------------------------------------------------------------------
        # 4.SPP alert 
        # ------------------------------------------------------------------
        curr_spp = normalized_data.get("SPP")
        curr_spm = self._get_total_spm(normalized_data)

        curr_spp = self._apply_factor(
            "SPP",
            normalized_data.get("SPP"),
            self.ranges.get("SPP", {}).get("factor"),
        )

        curr_spm = self._apply_factor(
            "SPM",
            self._get_total_spm(normalized_data),
            self.ranges.get("SPM", {}).get("factor"),
        )

        self.log.warning(
            "DEBUG 1 | SPP=%s | SPM=%s | MP1=%s | MP2=%s | MP3=%s | MP4=%s",
            curr_spp,
            curr_spm,
            normalized_data.get("MP1_SPM"),
            normalized_data.get("MP2_SPM"),
            normalized_data.get("MP3_SPM"),
            normalized_data.get("MP4_SPM"),
        )

        if (
            curr_spp is not None
            and curr_spm is not None
            and curr_spm > 0
        ):


            current_time = datetime.now()

     
            input_factor = self.spp_threshold / 100.0

    
            factor_elapsed = (
                current_time - self.spp_spm_factor_time
            ).total_seconds()

            self.log.warning(
                "Factor age = %.1fs",
                factor_elapsed
            )

            if factor_elapsed >= self.spp_factor_duration:

                self.spp_spm_factor = curr_spp / curr_spm

                self.spp_spm_factor_time = current_time

                self.log.warning(
                    "FACTOR UPDATED | SPP=%.4f | SPM=%.4f | Factor=%.4f",
                    curr_spp,
                    curr_spm,
                    self.spp_spm_factor,
                )



            if self.spp_spm_factor > 0:

                comparison_elapsed = (
                    current_time - self.spp_comparison_time
                ).total_seconds()

                self.log.warning(
                    "Comparison age = %.1fs",
                    comparison_elapsed
                )

                if comparison_elapsed >= 5:

                    # Reset comparison timer
                    self.spp_comparison_time = current_time


                    calculated_spp = curr_spm * self.spp_spm_factor

                    upper_limit = calculated_spp + (
                        calculated_spp * input_factor
                    )

                    lower_limit = calculated_spp - (
                        calculated_spp * input_factor
                    )

                    self.log.warning(
                        "COMPARE | Factor=%.4f | Calculated SPP=%.4f | "
                        "Current SPP=%.4f | Upper=%.4f | Lower=%.4f",
                        self.spp_spm_factor,
                        calculated_spp,
                        curr_spp,
                        upper_limit,
                        lower_limit,
                    )


                    if upper_limit > calculated_spp:

                        self.log.warning(
                            "ALERT HIGH | %.2f > %.2f",
                            upper_limit,
                            calculated_spp,
                        )

                        raise_alert(
                            f"SPP is out of expected range",
                            subject="SPP_SPM_FACTOR",
                            value=f"{calculated_spp:.2f}",
                        )


                    elif lower_limit < calculated_spp:

                        self.log.warning(
                            "ALERT LOW | %.2f < %.2f",
                            lower_limit,
                            calculated_spp,
                        )

                        raise_alert(
                            f"SPP is out of expected range",
                            subject="SPP_SPM_FACTOR",
                            value=f"{calculated_spp:.2f}",
                        )

                    # ------------------------------------------------------
                    # NO ALERT
                    # ------------------------------------------------------

                    else:

                        self.log.warning(
                            "NO ALERT | %.2f is within %.2f and %.2f",
                            calculated_spp,
                            lower_limit,
                            upper_limit,
                        ) 



        # ------------------------------------------------------------------
        # 6. ROP change
        # ------------------------------------------------------------------
        # In the same unit as the limits, not the raw column. Read raw, the
        # check was backwards for a rig storing minutes per metre: a value
        # going up means the rig is drilling SLOWER, so "ROP increased by
        # 100%" was raised on a bit that had just halved its rate of
        # penetration, and a bit that doubled it raised nothing at all.
        rop_raw = normalized_data.get("ROP")
        rop = self._in_limit_unit("ROP", rop_raw)

        if rop_raw is not None and rop is None:
            # Not advancing - a connection or a trip. Nothing to compare, and
            # the baseline goes with it so the next spell of drilling is
            # measured from where it starts rather than from before the trip.
            self.previous_rop = None
            self.previous_rop_time = None

        elif rop is not None:
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

                    self.log.debug("ROP %s -> %s over %.1fs = %.2f%%",
                              self.previous_rop, rop, elapsed, percent_change)

                    if percent_change > self.rop_threshold:
                        raise_alert(
                            f"[{date_str}] {self.display_name('ROP')} increased by {percent_change:.2f}%, BD:{bit_depth}{depth_unit}",
                            "ROP",
                            subject="ROP_CHANGE",
                            value=f"increased {percent_change:.2f}",
                            why=self.alert_log.change_reason(
                                "ROP", self.previous_rop, rop, elapsed,
                                percent_change, self.rop_threshold,
                                self.rop_duration, raw=rop_raw,
                            ),
                        )

                    else:
                        self._last_alerted.pop("ROP_CHANGE", None)

                    self.previous_rop = rop
                    self.previous_rop_time = current_time

                    rop_percentage = round(percent_change, 2)
        # ------------------------------------------------------------------
        # 7. Hookload unchanged
        #
        # A hookload that does not move at all is the sign of a stalled feed:
        # the rig is still sending rows but the values in them are frozen.
        # ------------------------------------------------------------------

        hookload = normalized_data.get("HOOKLOAD")

        if hookload is not None:

            current_time = datetime.now()

            # First reading
            if self.previous_hookload is None:
                self.previous_hookload = hookload
                self.previous_hookload_time = current_time

            elif hookload == self.previous_hookload:

                elapsed = (
                    current_time - self.previous_hookload_time
                ).total_seconds()

                if elapsed >= self.hookload_duration:
                    raise_alert(
                        f"[{date_str}] Please check for data Trans. {self.display_name('HOOKLOAD')} has remained unchanged for {int(elapsed)} seconds",
                        "HOOKLOAD",
                        subject="HOOKLOAD_STUCK",
                        why=self.alert_log.stuck_reason(
                            "HOOKLOAD", hookload, elapsed, self.hookload_duration,
                        ),
                    )
                    # Reset the timer so the alert does not fire every second
                    # for as long as the value stays stuck.
                    self.previous_hookload_time = current_time

            else:
                # Value moved -> start measuring again from here.
                self.previous_hookload = hookload
                self.previous_hookload_time = current_time

                self._last_alerted.pop("HOOKLOAD_STUCK", None)



        # ------------------------------------------------------------------
        # Depth Jump alert
        # ------------------------------------------------------------------
        bit_depth = normalized_data.get("BIT_DPT_MD")
        self.log.warning(
            "BIT_DPT_MD VALUE = %s",
            bit_depth,
        )

        if self.last_bit_depth is not None:

                difference = abs(bit_depth - self.last_bit_depth)

                self.log.warning(
                    "BIT DEPTH CHECK |current=%s | Previous=%s | Threshold=%.2f",
                    bit_depth,
                    f"{self.last_bit_depth:.2f}" if self.last_bit_depth is not None else "None",
                    self.bit_depth_threshold,
                )

                if difference > self.bit_depth_threshold:
                    raise_alert(
                            (
                                f"[{date_str}] "
                                f"last depth : {self.last_bit_depth} | "
                                f"current depth : {bit_depth} " 
                                f"Bit Depth jump by {difference:.2f}{depth_unit} "
                            ),
                            "BIT_DPT_MD",
                            subject="BIT_DEPTH_CHANGE",
                            value=round(difference, 2),
                        )

        self.last_bit_depth = bit_depth

        

        # ------------------------------------------------------------------

        # A problem that has gone away has cleared: if it comes back, even
        # saying exactly the same thing, that is a new alert.
        for subject in list(self._last_alerted):
            if subject not in raised and subject not in EVENT_SUBJECTS:
                del self._last_alerted[subject]

        self.alert_log.reading(
            activity, bit_depth, ta, tg,
            spp_percentage, totalspm_percentage, rop_percentage,
            standing, standing_sources,
            reasons=standing_reasons,
            normalized_data=normalized_data,
        )

        return ValidationResult(
            alerts=alerts,
            standing=len(standing),
            spp_percentage=spp_percentage,
            totalspm_percentage=totalspm_percentage,
            rop_percentage=rop_percentage,
            activity=activity,
            normalized=normalized_data,
        )


    def reset_state(self):
        """Clear timers/baselines - use when the source table is switched."""
        self.ta_gt_tg_start = None
        self.previous_spp = self.previous_spp_time = None
        self.previous_totalspm = self.previous_totalspm_time = None
        self.previous_rop = self.previous_rop_time = None
        self.previous_hookload = self.previous_hookload_time = None
        self._last_activity = None
        self._last_alerted.clear()
        self.alert_log.reset()
        self.log.info("Validator state reset")


# ----------------------------------------------------------------------
# Building one
# ----------------------------------------------------------------------

def build_validator(sample_row, rules, rules_path=None, well=None):
    """
    A validator for one well, from that well's own rules.

    `sample_row` is the first row read from its table: SELECT * gives every
    column, which is what the mapping is matched against. Resolving here rather
    than on the first check means a rig whose columns are named differently is
    reported at startup.

    `rules_path` is the file those rules came from. Given one, the validator
    re-reads it whenever it changes, so an edit in the dashboard takes effect
    without restarting the agent.

    `well` is the database name, and is what every line this validator logs is
    filed under - without it a shared log cannot say which rig raised what.
    """
    mapper = ColumnMapper.from_mapping(
        rules["column_mapping"],
        derived=DERIVED_PARAMS,
        optional=OPTIONAL_PARAMS,
    )
    mapper.resolve(list(sample_row.keys()))

    return RealtimeValidator(
        mapper=mapper,
        ranges=rules["ranges"],
        activity_rules=rules["activity"],
        conditions=rules["conditions"],
        drilling_criteria=rule_files.drilling_criteria_of(rules),
        bit_depth_threshold=rules.get("bit_depth_threshold",rule_files.DEFAULT_BIT_DEPTH_THRESHOLD),
        rules_path=rules_path,
        well=well,
    )
