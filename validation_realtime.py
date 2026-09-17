"""
Realtime QC checks - the rules that turn one reading into a list of alerts.

One RealtimeValidator per well. It holds the state the change checks need
(the previous SPP, SPM, ROP and HOOKLOAD readings and when each was taken),
so it cannot be shared between wells - build one per agent.

Seven checks run on every reading, in this order:

  1. activity      DRILLING or NON DRILLING, from hole depth minus bit depth, then
                   every parameter activity.json marks 1 must be above 0
  2. ranges        each parameter inside its min/max from ranges.json,
                   after `factor` converts it into the limits' unit
  3. TA > TG       alert once TA has been above TG for TA_TG.duration_seconds
  4. SPP           percentage move over SPP.duration_seconds, either direction
  5. SPM           the same, for total pump strokes per minute
  6. ROP           the same, but an increase only - a drop to zero is normal
                   whenever the bit comes off bottom
  7. HOOKLOAD      alert when the value has not moved at all for
                   HOOKLOAD.duration_seconds, which means a stalled feed

Rules come from data/*.json and are re-read when those files change, without
a restart. Parameters are referred to by logical name throughout (SPP, ROP,
HOOKLOAD ...); ColumnMapper is what turns those into this table's columns.
"""

from importlib import resources
from email import message
from fastapi import staticfiles
from importlib import resources
from fastapi import param_functions
from config import Config
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import well_rules
from logger import get_logger
import rule_files
from column_mapper import ColumnMapper, to_number
from rule_files import DRILLING, NON_DRILLING, RuleFileError


log = get_logger(__name__)
alert_log = get_logger("qc.alerts")

# How often a set of alerts that has not changed is written out again.
LOG_REPEAT_SECONDS = 60

# The timestamp every alert starts with, "[17-09-26 05-32-43]". The alerts
# endpoint and the agent read it back to tell how old an alert is.
ALERT_TIME_FORMAT = "%d-%m-%y %H-%M-%S"

_ALERT_STAMP = re.compile(r"^\[([^\]]*)\]")

# Subjects raised by checks that only look once per window (or, for HOOKLOAD,
# once per stall period). A reading in between says nothing about them, so
# they are not cleared just for not being raised - their own check clears them
# when it looks and finds nothing.
EVENT_SUBJECTS = {"SPP_CHANGE", "SPM_CHANGE", "ROP_CHANGE", "HOOKLOAD_STUCK"}

_TIMESTAMP_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


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


def _alert_fingerprint(alert):
    return _NUMBER.sub(
        "#",
        _TIMESTAMP_PREFIX.sub("", alert)
    )


@dataclass
class ValidationResult:
    alerts: list = field(default_factory=list)
    spp_percentage: float = 0.0
    totalspm_percentage: float = 0.0
    rop_percentage: float = 0.0
    activity: str = None
    normalized: dict = field(default_factory=dict)


class RealtimeValidator:
    def __init__(self, mapper, ranges, activity_rules, conditions, drilling_criteria,
                 rules_path=None):
        self.mapper = mapper
        self.ranges = ranges
        self.activity_rules = activity_rules
        self.conditions = conditions
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

        # What the well's rule file looked like when it was last read in.
        self._rule_stamp = _stamp(self.rules_path)

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

        self.totalspm_threshold = conditions["SPM"]["percentage_change"]
        self.totalspm_duration = conditions["SPM"]["duration_seconds"]

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

            mapper = ColumnMapper.from_mapping(rules["column_mapping"])
            mapper.resolve(list(row.keys()))

            self.mapper = mapper
            self.ranges = rules["ranges"]
            self.activity_rules = rules["activity"]
            self.conditions = rules["conditions"]
            self.drilling_criteria = float(
                rules.get("drilling_criteria", 0.1)
            )

            self._apply_conditions()

        except Exception as exc:
            log.error(
                "%s changed but could not be loaded (%s) - carrying on with "
                "the rules already in memory",
                Path(self.rules_path).name, exc,
            )
            return False

        log.info("Reloaded rules from %s", Path(self.rules_path).name)

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
            log.error(
                "display_name.json could not be loaded (%s) - alerts keep the "
                "names already in memory", exc,
            )
            return

        if not isinstance(names, dict):
            log.error(
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

        log.info("Display names loaded: %d", len(self.display_names))

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
    def _apply_factor(self, param, value, factor):
        """
        The reading converted into the unit its limits are written in.

        `factor` in ranges.json is a multiplier: the stored value is multiplied
        by it before being compared, and the converted number is what the alert
        quotes. Leave it out and the column is compared as it is stored.

        A factor that is not a usable number is reported and ignored rather
        than allowed to skip the check.
        """
        if factor is None:
            return value

        multiplier = to_number(factor)

        if multiplier is None or multiplier == 0:
            log.error(
                "ranges.json: '%s' has factor %r, which is not a usable "
                "multiplier - comparing the raw value instead",
                param, factor,
            )
            return value

        return round(value * multiplier, 4)


    def _get_total_spm(self, normalized_data):
        total_spm = 0

        for pump in ["MP1_SPM", "MP2_SPM", "MP3_SPM", "MP4_SPM", "MP5_SPM"]:
            value = normalized_data.get(pump)

            if value is not None:
                total_spm += value

        return total_spm


    # ------------------------------------------------------------------
    def detect_activity(self, data, raise_alert, date_str):

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
            )
            return None

        gap = total_depth - bit_depth

        activity = (
            "DRILLING"
            if gap <= self.drilling_criteria
            else "NON DRILLING"
        )

        if activity != self._last_activity:
            log.debug(
                "Activity %s (hole %s - bit %s = %.2f m)",
                activity, total_depth, bit_depth, gap,
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
            log.warning("Row normalisation: %s", err)

        # New alerts only - what gets saved and shown on the dashboard.
        alerts = []
        sources = []

        # Everything wrong on this reading, repeats included - what the log
        # reports, so a problem that is still there is not logged as cleared.
        standing = []
        standing_sources = []

        raised = set()

        def raise_alert(message, *params, subject, value=None):
            """
            Record a problem, unless it repeats the last alert about the same thing.

            `subject` is what the alert is about (ROP_CHANGE, RANGE:HOOKLOAD,
            ZERO:SPP ...). `value` is what it says about it - the part of the
            message that matters, leaving out the timestamp and the bit depth.
            "ROP increased by 12%" at BD 2239 and again at BD 2240 is the same
            alert and is saved once; "ROP increased by 30%" is a new one.
            """
            raised.add(subject)
            standing.append(message)
            standing_sources.append(params)

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
                )
                log.error("activity.json has no rule block for '%s'", activity)

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
                log.error(
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

            # `factor` converts the stored reading into the unit the limits are
            # written in, before either is compared. ROP is the reason it
            # exists: the table stores it in one unit and ranges.json is in
            # m/hr. Without this the limits were being applied to the raw
            # column, which is the unit they were never written for.
            if param.upper() == "ROP":
                try:
                    if value != 0:
                        value = 60 / float(value)
                    else:
                        log.warning("ROP is 0, skipping 60/ROP conversion")
                        continue
                except Exception as e:
                    log.error(f"Failed to convert ROP value {value}: {e}")
                    continue
            else:
                value = self._apply_factor(param, value, limits.get("factor"))
       
            
            if value < min_val:
                raise_alert(
                    f"[{date_str}] {self.display_name(param)} : {value:.2f}{unit} below limit {min_text}{unit} BD : {bit_depth}{depth_unit} ",
                    param,
                    subject=f"RANGE:{param}",
                    value=f"below {value:.2f}",
                )

            elif value > max_val:
                raise_alert(
                    f"[{date_str}] {self.display_name(param)} : {value:.2f}{unit} above limit {max_text}{unit}  BD : {bit_depth}{depth_unit}",
                    param,
                    subject=f"RANGE:{param}",
                    value=f"above {value:.2f}",
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
                        f"[{date_str}] {self.display_name('TA')} is greater than "
                        f"{self.display_name('TG')} where BD:{bit_depth}",
                        "TA",
                        "TG",
                        subject="TA_TG",
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
                            f"[{date_str}] {self.display_name('SPP')} increased by {percent_change:.2f}% where BD:{bit_depth}{depth_unit}",
                            "SPP",
                            subject="SPP_CHANGE",
                            value=f"increased {percent_change:.2f}",
                        )

                    elif percent_change < -self.spp_threshold:
                        raise_alert(
                            f"[{date_str}] {self.display_name('SPP')} dropped by {abs(percent_change):.2f}% where BD:{bit_depth}{depth_unit}",
                            "SPP",
                            subject="SPP_CHANGE",
                            value=f"dropped {abs(percent_change):.2f}",
                        )

                    else:
                        # Looked and found a steady SPP: the next move is a new
                        # alert even if it is the same size as the last one.
                        self._last_alerted.pop("SPP_CHANGE", None)

                    # Reset baseline
                    self.previous_spp = spp
                    self.previous_spp_time = current_time

                    spp_percentage = round(percent_change, 2)

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

        #             log.debug("TotalSPM %s -> %s over %.1fs = %.2f%%",
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
                            f"[{date_str}] {self.display_name('ROP')} increased by {percent_change:.2f}% Where BD:{bit_depth}{depth_unit}",
                            "ROP",
                            subject="ROP_CHANGE",
                            value=f"increased {percent_change:.2f}",
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

        # A problem that has gone away has cleared: if it comes back, even
        # saying exactly the same thing, that is a new alert.
        for subject in list(self._last_alerted):
            if subject not in raised and subject not in EVENT_SUBJECTS:
                del self._last_alerted[subject]

        self._log_reading(
            activity, bit_depth, ta, tg,
            spp_percentage, totalspm_percentage, rop_percentage,
            standing, standing_sources,
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

        `alerts` here is everything wrong on this reading, repeats included -
        not only the new alerts that were saved - so a problem that is still
        there is never logged as all clear.
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

    def reset_state(self):
        """Clear timers/baselines - use when the source table is switched."""
        self.ta_gt_tg_start = None
        self.previous_spp = self.previous_spp_time = None
        self.previous_totalspm = self.previous_totalspm_time = None
        self.previous_rop = self.previous_rop_time = None
        self.previous_hookload = self.previous_hookload_time = None
        self._last_fingerprint = None
        self._unchanged_since = self._last_logged = None
        self._last_activity = None
        self._last_alerted.clear()
        log.info("Validator state reset")


# ----------------------------------------------------------------------
# Building one
# ----------------------------------------------------------------------

def build_validator(sample_row, rules, rules_path=None):
    """
    A validator for one well, from that well's own rules.

    `sample_row` is the first row read from its table: SELECT * gives every
    column, which is what the mapping is matched against. Resolving here rather
    than on the first check means a rig whose columns are named differently is
    reported at startup.

    `rules_path` is the file those rules came from. Given one, the validator
    re-reads it whenever it changes, so an edit in the dashboard takes effect
    without restarting the agent.
    """
    mapper = ColumnMapper.from_mapping(rules["column_mapping"])
    mapper.resolve(list(sample_row.keys()))

    return RealtimeValidator(
        mapper=mapper,
        ranges=rules["ranges"],
        activity_rules=rules["activity"],
        conditions=rules["conditions"],
        drilling_criteria=float(
            rules.get("drilling_criteria", 0.1)
        ),
        rules_path=rules_path,
    )
