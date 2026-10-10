"""
Realtime QC checks - the rules that turn one reading into a list of alerts.

One RealtimeValidator per well. It holds the state the checks need between
readings (the learned SPP/SPM ratio, the ROP window, how long HOOKLOAD has
been unchanged, the last bit depth), so it cannot be shared between wells -
build one per agent.

Seven checks run on every reading, in this order, each in its own file under
alerts_logic/:

  1. activity      DRILLING or NON DRILLING, from hole depth minus bit depth
                   against this well's drilling_criteria, then every parameter
                   its activity block marks 1 must be above 0
  2. ranges        each parameter inside its min/max from its ranges block,
                   after `factor` converts the reading into the limits' unit -
                   multiplying, or dividing for ROP (see INVERSE_PARAMS)
  3. TA > TG       alert once TA has been above TG for TA_TG.duration_seconds
  4. SPP           SPP against SPM x an SPP/SPM ratio learned from the well,
                   outside SPP.percentage_change either way
  5. ROP           above its rolling average over ROP.duration_seconds by more
                   than ROP.percentage_change - increase only, since a drop to
                   zero is normal whenever the bit comes off bottom
  6. HOOKLOAD      alert when the value has not moved at all for
                   HOOKLOAD.duration_seconds, which means a stalled feed
  7. bit depth     a move between two readings above this well's threshold
                   for the activity

Rules come from the well's own file in data/wells/ and are re-read when it
changes, without a restart - so two rigs can disagree about their column
names, their limits and how far off bottom still counts as drilling.
Parameters are referred to by logical name throughout (SPP, ROP, HOOKLOAD
...); ColumnMapper is what turns those into this table's columns.
"""


from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import rule_files
import well_rules
from alert_log import AlertLog
from column_mapper import ColumnMapper, to_number
from config import Config
from logger import get_logger
from rule_files import RuleFileError
from collections import deque
from alerts_logic import (
    ALERT_TIME_FORMAT,
    EVENT_SUBJECTS,
    INVERSE_PARAMS,
    PUMPS,
    DERIVED_PARAMS,
    OPTIONAL_PARAMS,
    detect_activity as _detect_activity,
    run_zero_checks,
    run_range_checks,
    run_ta_tg_check,
    run_spp_check,
    run_rop_check,
    run_hookload_check,
    run_bit_depth_check,
)
import json
import os

log = get_logger(__name__)

# Parameters whose ranges "max" may be raised while the agent runs.
ADJUSTABLE_MAX = ("HOOKLOAD", "SPP", "ROP", "WOB")


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


@dataclass
class ValidationResult:
    alerts: list = field(default_factory=list)

    # Everything wrong on this reading, repeats included. `alerts` is only what
    # was new, so a well with a standing problem reports 0 new alerts a second
    # later - which reads as "nothing wrong" unless this is there too.
    standing: int = 0

    activity: str = None


class RealtimeValidator:
    def __init__(self, mapper, ranges, activity_rules, conditions, drilling_criteria,bd_threshold_drillign,bd_threshold_non_drilling,
                 rules_path=None, well=None):

        self.well = well or "unknown-well"
        self.log = get_logger(f"validation.{self.well}")

    
        self.alert_log = AlertLog(self.well)

        self.mapper = mapper
        self.ranges = ranges
        self.activity_rules = activity_rules
        self.conditions = conditions

        self.drilling_criteria = drilling_criteria

        self._last_alerted = {}


        # The well file these rules came from, watched for edits. None means
        # nothing to watch - the rules were handed in directly.
        self.rules_path = rules_path
 
       
        self.max_adjust = {}    # param -> (percentage, window seconds)
        self.max_windows = {}   # param -> {"values": [...], "start": datetime}
        self._apply_conditions()

        # ---- persistent state (was module-level globals) ----
        self.ta_gt_tg_start = None

        # SPP: the SPP/SPM ratio learned from the well, when it was last
        # learned, when SPP was last compared against it, and how often it is
        # learned again.
        self.spp_spm_factor = 0.0
        self.spp_spm_factor_time = datetime.now()
        self.spp_comparison_time = datetime.now()
        self.spp_factor_duration = 60.0

        # ROP: (time, m/hr) for the readings in the rolling window.
        self.rop_history = deque()

        # Hookload: the value it is stuck at and since when, and whether the
        # stuck alert is up and when it was last raised.
        self.previous_hookload = None
        self.previous_hookload_time = None
        self.hookload_alert_active = False
        self.hookload_last_alert_time = None

        # What the last reading was called, so a change of activity is logged
        # once rather than every second. What was last written to the alert log
        # is the alert log's own business - see AlertLog.
        self._last_activity = None

        self.last_bit_depth = None
        self.bd_threshold_drillign = bd_threshold_drillign
        self.bd_threshold_non_drilling = bd_threshold_non_drilling

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

        self.rop_threshold = conditions["ROP"]["percentage_change"]
        self.rop_duration = conditions["ROP"]["duration_seconds"]

        hook = conditions["HOOKLOAD"]
        self.hookload_duration = hook["duration_seconds"]

        self._load_max_adjust()



    def _load_max_adjust(self):
        """Which parameters have their max adjusted, and with what settings."""
        self.max_adjust = {}
        self.max_windows = {}

        for param in ADJUSTABLE_MAX:
            block = self.conditions.get(param)

            if not isinstance(block, dict):
                continue

            pct = to_number(block.get("percentage_change"))
            seconds = to_number(block.get("duration_seconds"))

            if pct is None or seconds is None or seconds <= 0:
                continue

            self.max_adjust[param] = (pct, seconds)

        if not self.max_adjust:
            self.log.info("Max adjustment OFF - no parameter has its settings in conditions")
            return

        for param, (pct, seconds) in self.max_adjust.items():
            self.log.info(
                "%s max adjustment ON - +%s%% over %ss windows", param, pct, seconds
            )

    def update_adaptive_maxes(self, normalized_data, now):
        for param, (pct, seconds) in self.max_adjust.items():
            self._update_max(param, pct, seconds, normalized_data.get(param), now)

    def _update_max(self, param, pct, seconds, raw, now):
        """
        Once per `seconds`: average the parameter (in the limits' unit), add
        `pct` percent, and if that is above the current max make it the new max.
        """
        limits = self.ranges.get(param)

        if not limits or raw is None:
            return

        value = self._in_limit_unit(param, raw)

        # ROP at 0 has no speed to average; the window just skips that reading.
        if value is None:
            return

        window = self.max_windows.setdefault(param, {"values": [], "start": None})

        if window["start"] is None:
            window["start"] = now

        window["values"].append(value)

        elapsed = (now - window["start"]).total_seconds()

        if elapsed < seconds:
            return   # window not finished yet

        values = window["values"]
        samples = len(values)
        avg = sum(values) / samples
        low, high = min(values), max(values)

        window["values"] = []
        window["start"] = None

        calculated = round(avg + avg * pct / 100, 2)

        current_max = to_number(limits.get("max"))

        if current_max is None:
            return

        # Optional hard ceiling, written in the same ranges block.
        cap = to_number(limits.get("absolute_max"))
        capped = cap is not None and calculated > cap

        if capped:
            calculated = cap

        self.log.debug(
            "%s window done | %.0fs, %d samples | min=%.2f max_seen=%.2f avg=%.2f | "
            "cal=avg+%s%%=%.2f%s | current max=%.2f | %s",
            param, elapsed, samples, low, high, avg, pct, calculated,
            " (capped by absolute_max)" if capped else "",
            current_max,
            "cal > max -> UPDATE" if calculated > current_max
            else "cal <= max -> no change",
        )

        if calculated > current_max:
            self.log.info("%s max updated | %.2f -> %.2f", param, current_max, calculated)
            self._save_max(param, calculated)

    @staticmethod
    def _find_limits(node, param):
        """
        The {"min", "max", ...} block for `param`, wherever the well file nests
        it. Found by shape, not by the name of the key that holds it.
        """
        if isinstance(node, dict):
            block = node.get(param)

            if isinstance(block, dict) and "min" in block and "max" in block:
                return block

            for value in node.values():
                found = RealtimeValidator._find_limits(value, param)

                if found is not None:
                    return found

        elif isinstance(node, list):
            for value in node:
                found = RealtimeValidator._find_limits(value, param)

                if found is not None:
                    return found

        return None

    def _save_max(self, param, new_max):
        """Put the new max in memory and in the well's rule file."""
        self.ranges[param]["max"] = new_max

        if self.rules_path is None:
            return

        path = Path(self.rules_path)

        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)

            block = self._find_limits(doc, param)

            if block is None:
                self.log.error(
                    "%s max is %.2f in memory but %s has no min/max block for it "
                    "(top-level keys: %s)",
                    param, new_max, path.name,
                    list(doc.keys()) if isinstance(doc, dict) else type(doc).__name__,
                )
                return

            block["max"] = new_max

            temporary = path.with_name(path.name + ".writing")
            with open(temporary, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=4, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, path)

            # Our own write must not look like an outside edit.
            self._rule_stamp = _stamp(path)
            self.log.info("%s max %.2f saved to %s", param, new_max, path.name)

        except Exception as exc:
            self.log.error(
                "%s max is %.2f in memory but %s could not be updated (%r)",
                param, new_max, path.name, exc,
            )

    def reload_rules_if_changed(self, row):

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
            self.bd_threshold_drillign = rules.get("bd_threshold_drillign", rule_files.DEFAULT_BD_THRESHOLD_DRILLING)
            self.bd_threshold_non_drilling=rules.get("bd_threshold_non_drilling",rule_files.DEFAULT_BD_THRESHOLD_NON_DRILLING)
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

        Thin delegate onto alerts_logic.activity_check.detect_activity so
        anything already calling self.detect_activity(...) keeps working -
        the logic itself lives there now, unchanged.
        """
        return _detect_activity(self, data, raise_alert, date_str)

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

            raised.add(subject)
            standing.append(message)
            standing_sources.append(params)
            standing_reasons.append(why)

            if subject in self._last_alerted and self._last_alerted[subject] == value:
                return

            self._last_alerted[subject] = value
            alerts.append(message)
            sources.append(params)

        # One clock read for the whole reading: the stamp every alert carries
        # and every elapsed time measured below come off the same instant,
        # rather than each check taking its own a few microseconds apart.
        now = datetime.now()

        date_str = now.strftime(ALERT_TIME_FORMAT)
        bit_depth = normalized_data.get("BIT_DPT_MD")
        total_depth = normalized_data.get("DEPTH")
        depth_unit = self.ranges.get("DEPTH", {}).get("unit", "")
        rop_unit = self.ranges.get("ROP",{}).get("unit","")
        spp_unit = self.ranges.get("SPP",{}).get("unit","")
        spm_unit = self.ranges.get("SPM",{}).get("unit","")

        # The pumps added up, once. The activity zero-check and the range
        # check below both ask for it, and nothing between them can change
        # what it comes to.
        total_spm = self._get_total_spm(normalized_data)

        # For the reading() call at the bottom - same values the TA>TG check
        # itself looks at.
        ta = normalized_data.get("TA")
        tg = normalized_data.get("TG")

        # ------------------------------------------------------------------
        # 1. Activity conditions
        # ------------------------------------------------------------------
        activity = self.detect_activity(normalized_data, raise_alert, date_str)

        if activity:
            run_zero_checks(
                self, activity, normalized_data, raise_alert, date_str,
                bit_depth, total_depth, depth_unit, total_spm,
            )

        # ------------------------------------------------------------------
        # 2. Ranges
        # ------------------------------------------------------------------
        self.update_adaptive_maxes(normalized_data, now)
        run_range_checks(
            self, normalized_data, raise_alert, date_str,
            bit_depth, depth_unit, total_spm,
        )

        # ------------------------------------------------------------------
        # 3. TA > TG
        # ------------------------------------------------------------------
        run_ta_tg_check(self, normalized_data, raise_alert, date_str, bit_depth, now)

        # ------------------------------------------------------------------
        # 4. SPP alert
        # ------------------------------------------------------------------
        run_spp_check(self, normalized_data,date_str, raise_alert,spp_unit,spm_unit)

        # ------------------------------------------------------------------
        # 6. ROP change
        # ------------------------------------------------------------------
        run_rop_check(
            self, normalized_data, raise_alert, date_str, bit_depth, depth_unit,rop_unit, now,
        )

        # ------------------------------------------------------------------
        # 7. Hookload unchanged
        # ------------------------------------------------------------------
        run_hookload_check(self, normalized_data, raise_alert, date_str, now)

        # ------------------------------------------------------------------
        # Depth Jump alert
        # ------------------------------------------------------------------
        if activity == "DRILLING":
            threshold = self.bd_threshold_drillign
        else:
            threshold = self.bd_threshold_non_drilling

        run_bit_depth_check(
            self,
            bit_depth,
            raise_alert,
            date_str,
            depth_unit,
            threshold,
        )

        # ------------------------------------------------------------------

        # A problem that has gone away has cleared: if it comes back, even
        # saying exactly the same thing, that is a new alert. The stuck
        # hookload is the exception - it stays up for as long as its own
        # check says the feed is still frozen.
        for subject in list(self._last_alerted):

            if (
                subject == "HOOKLOAD_STUCK"
                and self.hookload_alert_active
            ):
                continue

            if subject not in raised and subject not in EVENT_SUBJECTS:
                del self._last_alerted[subject]

        self.alert_log.reading(
            activity, bit_depth, ta, tg,
            standing, standing_sources,
            reasons=standing_reasons,
            normalized_data=normalized_data,
        )

        return ValidationResult(
            alerts=alerts,
            standing=len(standing),
            activity=activity,
        )


# ----------------------------------------------------------------------
# Building one
# ----------------------------------------------------------------------

def build_validator(sample_row, rules, rules_path=None, well=None):

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
        bd_threshold_drillign=rules.get("bd_threshold_drillign",rule_files.DEFAULT_BD_THRESHOLD_DRILLING),
        bd_threshold_non_drilling=rules.get("bd_threshold_non_drilling",rule_files.DEFAULT_BD_THRESHOLD_NON_DRILLING),
        rules_path=rules_path,
        well=well,
    )



