"""
Check 1 - activity.

DRILLING or NON DRILLING, from hole depth minus bit depth against this
well's drilling_criteria, then every parameter its activity block marks 1
must be above 0.
"""

from datetime import datetime

from rule_files import DRILLING, NON_DRILLING

from .constants import ALERT_TIME_FORMAT, PUMPS


def detect_activity(validator, data, raise_alert, date_str):
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
            f"{validator.display_name('DEPTH')}={total_depth}, "
            f"{validator.display_name('BIT_DPT_MD')}={bit_depth}",
            "DEPTH", "BIT_DPT_MD",
            subject="ACTIVITY_UNDETERMINED",
            value=(total_depth is None, bit_depth is None),
            why=(
                f"activity needs both depths: DEPTH={total_depth} "
                f"(column {validator.mapper.column_for('DEPTH')}), "
                f"BIT_DPT_MD={bit_depth} "
                f"(column {validator.mapper.column_for('BIT_DPT_MD')}) - "
                "one of them is missing or not a number in this row"
            ),
        )
        return None

    gap = total_depth - bit_depth

    activity = DRILLING if gap <= validator.drilling_criteria else NON_DRILLING

    if activity != validator._last_activity:
        # The margin is logged with the gap: "why is this well DRILLING at
        # 0.4 m off bottom" is answered by the two numbers together.
        validator.log.debug(
            "Activity %s (hole %s - bit %s = %.2f m, criteria %g m)",
            activity, total_depth, bit_depth, gap, validator.drilling_criteria,
        )
        validator._last_activity = activity

    return activity


def run_zero_checks(validator, activity, normalized_data, raise_alert,
                     date_str, bit_depth, total_depth, depth_unit, total_spm):
    """
    Every parameter this activity's rule block marks mandatory (1) must be
    above 0 - e.g. SPM cannot be 0 while DRILLING.

    The "is 0" alert is saved once, when it starts. A flag per parameter
    remembers when that was, and on the first reading the parameter is no
    longer 0 one "resumed" alert gives the time it came back.
    """
    rules = validator.activity_rules.get(activity)

    if not rules:
        raise_alert(
            f"[{date_str}] Unknown activity: {activity}",
            subject="ACTIVITY_UNKNOWN",
            value=activity,
            why=(
                f"the rig is {activity} but this well's activity rules "
                f"only cover {', '.join(validator.activity_rules) or 'nothing'} - "
                "no zero-checks could be run for this reading"
            ),
        )
        validator.log.error("activity.json has no rule block for '%s'", activity)
        return

    # The parameters that are 0 on this reading.
    zero_now = set()

    for param, is_mandatory in rules.items():

        # Only validate parameters marked as mandatory
        if is_mandatory != 1:
            continue

        if param == "SPM":

            value = total_spm

            if value <= 0:
                raise_alert(
                    f"[{date_str}] {validator.display_name(param)} is 0 where BD:{bit_depth:.2f}{depth_unit}, MD:{total_depth:.2f}{depth_unit}",
                    "SPM",
                    subject="ZERO:SPM",
                    value=activity,
                    why=(
                        f"activity[{activity}] requires SPM above 0; "
                        f"pumps total {value} "
                        f"({validator.alert_log.pump_breakdown(normalized_data, PUMPS)})"
                    ),
                )
                zero_now.add(param)

            continue

        if not validator.mapper.is_available(param):
            continue

        value = normalized_data.get(param)
        if value is None or value <= 0:
            raise_alert(
                f"[{date_str}] {validator.display_name(param)} is 0 where BD:{bit_depth:.2f}{depth_unit}, MD:{total_depth:.2f}{depth_unit}",
                param,
                subject=f"ZERO:{param}",
                value=activity,
                why=validator.alert_log.zero_reason(param, activity, value),
            )
            zero_now.add(param)

    _report_resumed(
        validator, zero_now, raise_alert, date_str, bit_depth, depth_unit,
        normalized_data, total_spm,
    )


def _report_resumed(validator, zero_now, raise_alert, date_str, bit_depth,
                    depth_unit, normalized_data, total_spm):
    """
    Keep each parameter's "is 0" flag, and say when it resumed.

    `_zero_since` is param -> when it was first 0. A parameter that is 0 now
    and has no flag gets one; one that has a flag and is not 0 any more has
    resumed - one alert with the resume time, and the flag is dropped, so the
    next time it reads 0 is a new alert with a new start.

    Resumed means the value is above 0 again, not just that it is no longer
    checked: RPM that is 0 when the rig goes from DRILLING to NON DRILLING
    is still 0, and keeps its flag until it reads above 0.
    """
    now = datetime.strptime(date_str, ALERT_TIME_FORMAT)
    zero_since = validator.__dict__.setdefault("_zero_since", {})

    for param in zero_now:
        zero_since.setdefault(param, now)

    for param in [p for p in zero_since if p not in zero_now]:
        value = total_spm if param == "SPM" else normalized_data.get(param)

        if value is None or value <= 0:
            continue

        since = zero_since.pop(param)
        seconds = int((now - since).total_seconds())
        name = validator.display_name(param)

        validator.log.info(
            "ZERO:%s RESUMED | was 0 from %s to %s (%ss)",
            param, since.strftime("%H:%M:%S"), now.strftime("%H:%M:%S"), seconds,
        )

        where = f", BD:{bit_depth:.2f}{depth_unit}" if bit_depth is not None else ""

        raise_alert(
            f"[{date_str}] {name} has resumed (was 0 for {seconds} seconds){where}",
            param,
            subject=f"RESUMED:{param}",
            value=since.timestamp(),
            why=(
                f"{param} read 0 from {since.strftime('%H:%M:%S')} until this "
                f"reading, which reads {validator.alert_log.num(value)} from column "
                f"{validator.mapper.column_for(param) or 'pumps total'}"
            ),
        )