"""
Check 2 - ranges.

Each parameter inside its min/max from its ranges block, after `factor`
converts the reading into the limits' unit - multiplying, or dividing for
ROP (see INVERSE_PARAMS in constants.py).

The max of HOOKLOAD, SPP, ROP and WOB can be raised while the agent runs (see
RealtimeValidator.update_adaptive_maxes). It is raised in validator.ranges, so
the limits read below are always the current ones.

Each alert also says how long the parameter has been continuously out of
range ("... from 45s"). The clock starts on the first out-of-range reading,
keeps running while the reading stays on the same side of the limit, and is
dropped as soon as the reading is back inside the limits (or crosses to the
other side, which is a different alert).
"""

import time

from column_mapper import to_number

from .constants import INVERSE_PARAMS

# 0 = show exact elapsed seconds (5s, 10s, 15s ...).
# 60 = show whole minutes only (60s, 120s, 180s ...), so the text changes
# once a minute instead of every reading.
ROUND_UP_TO_SECONDS = 0


def _violation_seconds(validator, param, direction):
    starts = validator.__dict__.setdefault("_range_since", {})
    now = time.time()
    start = starts.setdefault((param, direction), now)
    secs = int(now - start)

    step = int(ROUND_UP_TO_SECONDS)
    if step > 0:
        secs = (secs // step + 1) * step

    return secs


def _clear_violation(validator, param, direction=None):
    """Forget the start time for one direction, or for both if none is given."""
    starts = validator.__dict__.get("_range_since")

    if not starts:
        return

    for d in (("below", "above") if direction is None else (direction,)):
        starts.pop((param, d), None)


def run_range_checks(validator, normalized_data, raise_alert, date_str,
                     bit_depth, depth_unit, total_spm):

            
    for param, limits in validator.ranges.items():
        
        if param == "SPM":
            value = total_spm

            if value <= 0:
                continue
        else:
            value = normalized_data.get(param)

            if value is None:
                continue

        if param == "HOOKLOAD" and value == 0:
            continue

        # min, max and factor are tolerated as strings in the file
        # ("60"), so they are coerced here rather than compared raw -
        # comparing a float against a str is a TypeError, and it would
        # take down the whole reading, not just this one check.
        min_val = to_number(limits.get("min"))
        max_val = to_number(limits.get("max"))

        if min_val is None or max_val is None:
            validator.log.error(
                "ranges.json: '%s' has a min/max that is not a number "
                "(%r / %r) - range check skipped",
                param, limits.get("min"), limits.get("max"),
            )
            continue

        # Quoted as they are written in the file, so a limit of 200 still
        # reads "200" in the alert and not "200.0". A HOOKLOAD max that has
        # been raised reads as its new value, because `limits` is the same
        # dict that was updated.
        min_text = limits["min"]
        max_text = limits["max"]

        unit = limits.get("unit", "")

        # `factor` converts the stored reading into the unit the limits
        # are written in, before either is compared. ROP is the reason it
        # exists: the table stores it in minutes per metre and the limits
        # are in m/hr. Without this the limits were being applied to the
        # raw column, which is the unit they were never written for.
        raw = value
        value = validator._in_limit_unit(param, value)
        # print(f"--------------------param={param}   value={value}-----------------------")
        if value is None:
            # ROP at 0: the bit is not advancing, which is normal on every
            # connection and trip. There is no speed to range-check, so
            # this reading says nothing about the parameter either way.
            validator.log.debug("%s is 0 - nothing to convert, range check skipped", param)
            continue


        if value < min_val:
            # Only the opposite side is reset; this side keeps its start time.
            _clear_violation(validator, param, "above")
            secs = _violation_seconds(validator, param, "below")

            raise_alert(
                f"[{date_str}] {validator.display_name(param)} : {value:.2f}{unit} "
                f"below limit {min_text}{unit} from {secs}s "
                f"BD : {bit_depth:.2f}{depth_unit}",
                param,
                subject=f"RANGE:{param}",
                value=f"below {value:.2f}",
                why=validator.alert_log.range_reason(
                    param, raw, value, limits, "below", min_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
            )

        elif value > max_val:
            _clear_violation(validator, param, "below")
            secs = _violation_seconds(validator, param, "above")

            raise_alert(
                f"[{date_str}] {validator.display_name(param)} : {value:.2f}{unit} "
                f"above limit {max_text}{unit} "
                f"BD : {bit_depth:.2f}{depth_unit} from {secs}s",
                param,
                subject=f"RANGE:{param}",
                value=f"above {value:.2f}",
                why=validator.alert_log.range_reason(
                    param, raw, value, limits, "above", max_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
            )

        else:
            # Back inside the limits: the next violation counts from 0 again.
            _clear_violation(validator, param)