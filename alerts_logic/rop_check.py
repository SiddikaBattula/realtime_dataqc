"""
Check 6 - ROP.

The reading, after `factor` (m/hr), against the average of the readings in
the last ROP.duration_seconds. Alert when it is above that average plus
ROP.percentage_change - added as m/hr, not as a percentage of the average.
Increase only: a drop to zero is normal whenever the bit comes off bottom,
and a reading of 0 clears the history so the next spell of drilling is
measured from where it starts.
"""


def run_rop_check(
    validator,
    normalized_data,
    raise_alert,
    date_str,
    bit_depth,
    depth_unit,
    rop_unit,
    now
):
    rop_raw = normalized_data.get("ROP")
    rop = validator._in_limit_unit("ROP", rop_raw)

    rop_percentage = 0.0

    if rop_raw is not None and rop is None:
        validator.rop_history.clear()
        return rop_percentage

    if rop is None:
        return rop_percentage

    current_time = now

    # Add current reading
    validator.rop_history.append((current_time, rop))

    validator.log.debug(
        "ROP SAMPLE ADDED | value=%s |history_size=%s",
        rop,
        len(validator.rop_history)
    )

    # Remove old values outside duration window
    while (
        validator.rop_history
        and (current_time - validator.rop_history[0][0]).total_seconds()
        > validator.rop_duration
    ):
        validator.rop_history.popleft()

    # Need enough data
    if len(validator.rop_history) < 2:
        return rop_percentage

    values = [value for _, value in validator.rop_history]

    avg_rop = sum(values) / len(values)

    threshold_value = avg_rop + validator.rop_threshold

    validator.log.debug(
        "ROP Avg=%.2f Current=%.2f Threshold=%.2f Duration=%ss",
        avg_rop,
        rop,
        threshold_value,
        validator.rop_duration,
    )

    if rop > threshold_value:

        rop_percentage = (
            ((rop - avg_rop) / avg_rop) * 100
            if avg_rop > 0
            else 0
        )

        raise_alert(
            f"[{date_str}] "
            f"{validator.display_name('ROP')} "
            f"is {rop:.2f}{rop_unit} "
            f"(Avg:{avg_rop:.2f}{rop_unit}) threshold:{threshold_value:.2f} "
            f"which exceeds the configured threshold of "
            f"{validator.rop_threshold}% , "
            f"BD:{bit_depth:.2f}{depth_unit}",
            "ROP",
            subject="ROP_CHANGE",
            value=f"{rop:.2f}",
            why=(
                f"ROP {rop:.2f}{rop_unit} is above the {validator.rop_duration}s "
                f"average {avg_rop:.2f}{rop_unit} ({len(values)} readings) + "
                f"conditions[ROP].percentage_change {validator.rop_threshold} = "
                f"{threshold_value:.2f}{rop_unit} - the setting is added as "
                f"{rop_unit}, not as a percentage; {rop_percentage:.2f}% above "
                f"the average; read {validator.alert_log.num(rop_raw)} from column "
                f"{validator.mapper.column_for('ROP')}, factor "
                f"{validator.ranges.get('ROP', {}).get('factor')} / "
                f"{validator.alert_log.num(rop_raw)}"
            ),
        )
    else:
        validator._last_alerted.pop("ROP_CHANGE", None)

    return round(rop_percentage, 2)

