"""
Check 1 - activity.

DRILLING or NON DRILLING, from hole depth minus bit depth against this
well's drilling_criteria, then every parameter its activity block marks 1
must be above 0.
"""

from rule_files import DRILLING, NON_DRILLING

from .constants import PUMPS


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

    for param, is_mandatory in rules.items():

        # Only validate parameters marked as mandatory
        if is_mandatory != 1:
            continue

        if param == "SPM":

            value = total_spm

            if value <= 0:
                raise_alert(
                    f"[{date_str}] {validator.display_name(param)} cannot be 0 in {activity} where BD:{bit_depth:.2f}{depth_unit}, MD:{total_depth:.2f}{depth_unit}",
                    "SPM",
                    subject="ZERO:SPM",
                    value=activity,
                    why=(
                        f"activity[{activity}] requires SPM above 0; "
                        f"pumps total {value} "
                        f"({validator.alert_log.pump_breakdown(normalized_data, PUMPS)})"
                    ),
                )

            continue

        if not validator.mapper.is_available(param):
            continue

        value = normalized_data.get(param)

        if value is None or value <= 0:
            raise_alert(
                f"[{date_str}] {validator.display_name(param)} cannot be 0 in {activity} where BD:{bit_depth}{depth_unit}, MD:{total_depth}{depth_unit}",
                param,
                subject=f"ZERO:{param}",
                value=activity,
                why=validator.alert_log.zero_reason(param, activity, value),
            )