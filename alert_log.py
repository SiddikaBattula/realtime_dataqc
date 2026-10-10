"""
What the log says about a reading, and why.

The checks live in validation_realtime.py; the sentences describing them live
here. Splitting them apart keeps the validator about drilling rules and this
module about wording, and means the format of the log can be changed without
reading a line of the arithmetic.

Two things are written per well:

    validation.<well>   what the checks are doing, DEBUG upwards
    qc.alerts.<well>    a block per reading whose alerts changed

The block is the point of the module. An alert on its own says a limit was
passed; it does not say what was read, off which column, converted how, or
against which number out of that well's rules - so settling one meant opening
the rules and working backwards. Each alert now carries the check's own
arithmetic, and the whole reading is printed under it:

    Well         : KJ-16
    Activity     : DRILLING - hole 2000.0m - bit 2000.0m = 0.0m off bottom,
                   within the 0.05m off-bottom margin
    Bit depth    : 2000.0m (column BIT_DPT_MD)
    Hole depth   : 2000.0m (column TOT_DPT_MD)
    TA / TG      : TA=2.5%  TG=5.0%
    Alerts       : 2
      1. ROP : 120.00m/hr above limit 100m/hr  BD : 2000.0m
           why : ranges[ROP] is 0 to 100m/hr; read 0.5 from column ROP,
                 60 / 0.5 = 120.0m/hr, which is above the max of 100m/hr
      2. Torque is 0 where BD:2000.0m, MD:2000.0m
           why : activity[DRILLING] requires ROT_TORQUE_AVG above 0; read 0.0
           from: ROT_TORQUE_AVG = ROT_TORQUE_AVG
    Readings     : DEPTH=2000.0m  ROP=120.0m/hr (raw 0.5)  WOB=0.0kflb  ...

Every value is shown in the unit its limits are written in - after `factor`,
as the checks compare it - with the raw column value beside it when the two
differ.

One AlertLog per well, held by that well's validator. It keeps the state that
decides when to write - what was last said, and when - so a problem that is
still there is not reprinted every second.
"""

import re

from datetime import datetime

from alerts_logic.constants import INVERSE_PARAMS
from column_mapper import to_number
from config import Config
from logger import get_logger

# The "[17-09-26 05-32-43]" an alert starts with. The log block carries its own
# timestamp, so the alert's comes off before it is printed.
_TIMESTAMP_PREFIX = re.compile(r"^\[[^\]]*\]\s*")

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _fingerprint(alert):
    """
    An alert with its numbers blanked out.

    Two readings a second apart say the same thing with a different bit depth
    in it. Comparing the blanked versions is what tells "the same problem is
    still there" from "something new has happened".
    """
    return _NUMBER.sub("#", _TIMESTAMP_PREFIX.sub("", alert))


def _short(number):
    """4 decimals at most, without trailing zeros: 50.1325, 2500.6, 0.0."""
    if not isinstance(number, (int, float)):
        return str(number)

    return f"{number:.4f}".rstrip("0").rstrip(".") if number % 1 else f"{number:.1f}"


class AlertLog:

    def __init__(self, well):
        self.well = well
        self.log = get_logger(f"qc.alerts.{well}")

        # The rules as they stand. Set by refresh(), and set again whenever the
        # validator reloads the well's file, so the sentences quote the limits
        # that are actually in force rather than the ones it started with.
        self.mapper = None
        self.ranges = {}
        self.drilling_criteria = None

        # What was last written, and when.
        self._last_fingerprint = None
        self._unchanged_since = None
        self._last_logged = None

    def refresh(self, mapper, ranges, drilling_criteria):
        """Point at the rules in force. Called on build and on every reload."""
        self.mapper = mapper
        self.ranges = ranges
        self.drilling_criteria = drilling_criteria

    # ------------------------------------------------------------------
    # Why an alert fired
    #
    # Each of these turns one check's arithmetic into a sentence. The alert
    # text says what is wrong; these say what was read, off which column,
    # converted how, and against which number out of the rules.
    # ------------------------------------------------------------------
    def range_reason(self, param, raw, value, limits, side, limit_text,
                     inverse=False):
        """Why a reading fell outside its range."""
        unit = limits.get("unit", "")
        factor = limits.get("factor")
        bound = "min" if side == "below" else "max"

        raw, value = _short(raw), _short(value)

        if factor is None:
            conversion = "compared as stored"
        elif inverse:
            conversion = f"{factor} / {raw} = {value}{unit}"
        else:
            conversion = f"{raw} x {factor} = {value}{unit}"

        return (
            f"ranges[{param}] is {limits.get('min')} to {limits.get('max')}{unit}; "
            f"read {raw} from column {self._column(param)}, {conversion}, "
            f"which is {side} the {bound} of {limit_text}{unit}"
        )

    def zero_reason(self, param, activity, value):
        """Why a parameter that must not be zero raised one."""
        return (
            f"activity[{activity}] requires {param} above 0; "
            f"read {_short(value)} from column {self._column(param)}"
        )

    def stuck_reason(self, param, value, elapsed, duration):
        """Why a value that has not moved raised one."""
        return (
            f"{param} has read exactly {_short(value)} from column {self._column(param)} "
            f"for {elapsed:.0f}s, past conditions[{param}] of {duration}s - "
            "a live feed moves"
        )

    @staticmethod
    def num(value):
        """A number as the log writes it - 0.4004, not 0.4003666666666667."""
        return _short(value)

    def pump_breakdown(self, normalized_data, pumps):
        """"MP1_SPM 0.0, MP2_SPM 0.0" - what the pumps read when SPM totalled 0."""
        parts = [
            f"{pump} {normalized_data.get(pump)}"
            for pump in pumps
            if self.mapper is not None and self.mapper.is_available(pump)
        ]

        return ", ".join(parts) or "no pump columns in this table"

    # ------------------------------------------------------------------
    # What everything else was doing
    # ------------------------------------------------------------------
    def readings_line(self, normalized_data):
        """
        Every parameter this table maps, with its value, on one line.

        The alert says what broke; this says what the rest of the rig was doing
        at the same instant, which is most of what a "why now?" question needs.
        """
        if self.mapper is None:
            return "no mapping yet"

        parts = []

        for param in self.mapper.mapping:
            if not self.mapper.is_available(param):
                continue

            parts.append(self.value_text(param, normalized_data.get(param)))

        return "  ".join(parts) or "no mapped columns"

    def value_text(self, param, raw):
        """
        "ROP=50.13m/hr (raw 1.1969)" - a reading in the unit its limits are
        written in, as the checks compare it.

        The unit belongs to the converted value, so a reading that `factor`
        changes is shown converted with the raw column value beside it. Printing
        the raw value with the unit on - "ROP=1.1969m/hr" for 50 m/hr - was
        wrong by a factor of 50.
        """
        unit = self._unit(param)
        value = self.in_limit_unit(param, raw)

        if raw is None or value is None:
            return f"{param}={raw}"

        if value == raw:
            return f"{param}={_short(value)}{unit}"

        return f"{param}={_short(value)}{unit} (raw {_short(raw)})"

    def _unit(self, param):
        """The parameter's unit; the bit depth is in the hole depth's."""
        if param == "BIT_DPT_MD" and "BIT_DPT_MD" not in self.ranges:
            param = "DEPTH"

        return self.ranges.get(param, {}).get("unit", "")

    def in_limit_unit(self, param, raw):
        """The reading after `factor`, as range_check compares it."""
        factor = to_number(self.ranges.get(param, {}).get("factor"))

        if raw is None or not factor:
            return raw

        if param.upper() in INVERSE_PARAMS:
            return None if raw == 0 else factor / raw

        return raw * factor

    def columns_note(self, params):
        """
        "Torque = ROT_TORQUE_AVG" for each logical name a check read.

        Only where the two differ: "WOB = WOB" says nothing the alert has not
        said already, and a dozen of those hide the one line that matters.
        """
        named = []

        for param in params:
            column = self._column(param)

            if column == "not in table":
                named.append(f"{param} = not in table")

            elif column.lower() != param.lower():
                named.append(f"{param} = {column}")

        return ", ".join(named)

    def _column(self, param):
        column = self.mapper.column_for(param) if self.mapper else None

        return column or "not in table"

    # ------------------------------------------------------------------
    # The block itself
    # ------------------------------------------------------------------
    def reading(self, activity, depth, ta, tg,
                alerts, sources, reasons=(), normalized_data=None):
        """
        One record per reading, never one per alert - and not once a second.

        Rows arrive about every second and mostly repeat what the last one
        said, so the full block is written when what is wrong actually changes:
        an alert appears, one clears, or the set of them is different. While
        the same alerts stay up it is repeated as a single line every
        LOG_REPEAT_SECONDS (.env), so the log keeps saying the problem is there
        without burying everything else. Every reading is still logged at DEBUG.

        `alerts` is everything wrong on this reading, repeats included - not
        only the new ones that were saved - so a problem that is still there is
        never logged as all clear.
        """
        now = datetime.now()
        data = normalized_data or {}

        summary = (
            f"{activity} | bit {self._depth('BIT_DPT_MD', data, depth, column=False)} "
            f"hole {self._depth('DEPTH', data, column=False)} | "
            f"{self.value_text('TA', ta)} {self.value_text('TG', tg)}"
        )

        readings = (
            self.readings_line(normalized_data)
            if normalized_data is not None else ""
        )

        self.log.debug("%s | %d alert(s) | %s", summary, len(alerts), readings)

        fingerprint = tuple(_fingerprint(alert) for alert in alerts)

        if fingerprint == self._last_fingerprint:
            self._repeat(now, summary, alerts, readings)
            return

        self._last_fingerprint = fingerprint
        self._unchanged_since = now
        self._last_logged = now

        if not alerts:
            self.log.info("%s | all clear", summary)
            return

        self.log.warning(
            "\n"
            f"Well         : {self.well}\n"
            f"Activity     : {self._activity_reason(activity, data, depth)}\n"
            f"Bit depth    : {self._depth('BIT_DPT_MD', data, depth)}\n"
            f"Hole depth   : {self._depth('DEPTH', data)}\n"
            f"TA / TG      : {self.value_text('TA', ta)}  {self.value_text('TG', tg)}\n"
            f"Alerts       : {len(alerts)}\n"
            + self._alert_lines(alerts, sources, reasons) + "\n"
            f"Readings     : {readings}\n"
        )

    def _depth(self, param, data, fallback=None, column=True):
        """"2503.18m (column TOT_DPT_MD)" for the block, "2503.18m" for the
        one-line summary."""
        value = data.get(param, fallback)
        unit = self._unit("DEPTH")
        where = f" (column {self._column(param)})" if column else ""

        if value is None:
            return f"none read{where}"

        return f"{_short(value)}{unit}{where}"

    def _activity_reason(self, activity, data, bit_fallback=None):
        """
        "NON DRILLING - hole 2503.18m - bit 2453.18m = 50m off bottom, more
        than the 0.1m margin": the activity and the sum that decided it.
        """
        hole = data.get("DEPTH")
        bit = data.get("BIT_DPT_MD", bit_fallback)
        unit = self.ranges.get("DEPTH", {}).get("unit", "")

        if hole is None or bit is None or activity is None:
            return f"{activity} - hole or bit depth missing, so it cannot be worked out"

        gap = hole - bit
        side = "within" if gap <= (self.drilling_criteria or 0) else "more than"

        return (
            f"{activity} - hole {_short(hole)}{unit} - bit {_short(bit)}{unit} = "
            f"{_short(gap)}{unit} off bottom, {side} the {self._criteria()}{unit} "
            "off-bottom margin"
        )

    def _alert_lines(self, alerts, sources, reasons):
        """The numbered alerts, each with its reason and its columns."""
        lines = []

        for number, alert in enumerate(alerts, 1):
            lines.append(f"  {number}. {_TIMESTAMP_PREFIX.sub('', alert)}")

            why = reasons[number - 1] if number <= len(reasons) else None

            # Every check gives one. Should a new check forget, the block says
            # so rather than leaving the alert looking explained.
            lines.append(f"       why : {why or 'no reason given by this check'}")

            note = self.columns_note(
                sources[number - 1] if number <= len(sources) else ()
            )

            if note:
                lines.append(f"       from: {note}")

        return "\n".join(lines)

    def _repeat(self, now, summary, alerts, readings):
        """The same as last time - say so occasionally, not every second."""
        if (now - self._last_logged).total_seconds() < Config.LOG_REPEAT_SECONDS:
            return

        self._last_logged = now
        held = int((now - self._unchanged_since).total_seconds())

        if alerts:
            self.log.warning(
                "%s | the same %d alert(s) have been up for %ds | %s",
                summary, len(alerts), held, readings,
            )
        else:
            self.log.info("%s | still clear after %ds", summary, held)

    def _criteria(self):
        return f"{self.drilling_criteria:g}" if self.drilling_criteria is not None else "?"
