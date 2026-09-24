"""
Check 6 - ROP.

Percentage move over ROP.duration_seconds, increase only - a drop to zero
is normal whenever the bit comes off bottom. Measured on the reading after
`factor`, so a rise means the bit is drilling faster and not that the raw
minutes-per-metre column went up.
"""


def run_rop_check(validator, normalized_data, raise_alert, date_str, bit_depth, depth_unit, now):
    rop_raw = normalized_data.get("ROP")
    rop = validator._in_limit_unit("ROP", rop_raw)

    rop_percentage = 0.0

    if rop_raw is not None and rop is None:
        # Not advancing - a connection or a trip. Nothing to compare, and
        # the baseline goes with it so the next spell of drilling is
        # measured from where it starts rather than from before the trip.
        validator.previous_rop = None
        validator.previous_rop_time = None
        return rop_percentage

    if rop is None:
        return rop_percentage

    current_time = now

    # First value
    if validator.previous_rop is None:
        validator.previous_rop = rop
        validator.previous_rop_time = current_time
        return rop_percentage

    # Prevent division by zero. ROP is legitimately 0 whenever the bit is
    # not advancing (tripping, connections, circulating), so without this
    # the next reading would divide by a zero baseline.
    if validator.previous_rop <= 0:
        validator.previous_rop = rop
        validator.previous_rop_time = current_time
        return rop_percentage

    elapsed = (current_time - validator.previous_rop_time).total_seconds()

    if elapsed < validator.rop_duration:
        return rop_percentage

    percent_change = ((rop - validator.previous_rop) / validator.previous_rop) * 100

    validator.log.debug("ROP %s -> %s over %.1fs = %.2f%%",
                         validator.previous_rop, rop, elapsed, percent_change)

    if percent_change > validator.rop_threshold:
        raise_alert(
            f"[{date_str}] {validator.display_name('ROP')} increased by {percent_change:.2f}%, BD:{bit_depth}{depth_unit}",
            "ROP",
            subject="ROP_CHANGE",
            value=f"increased {percent_change:.2f}",
            why=validator.alert_log.change_reason(
                "ROP", validator.previous_rop, rop, elapsed,
                percent_change, validator.rop_threshold,
                validator.rop_duration, raw=rop_raw,
            ),
        )
    else:
        validator._last_alerted.pop("ROP_CHANGE", None)

    validator.previous_rop = rop
    validator.previous_rop_time = current_time

    rop_percentage = round(percent_change, 2)

    return rop_percentage