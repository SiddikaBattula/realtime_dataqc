# """
# Check 6 - ROP.

# Percentage move over ROP.duration_seconds, increase only - a drop to zero
# is normal whenever the bit comes off bottom. Measured on the reading after
# `factor`, so a rise means the bit is drilling faster and not that the raw
# minutes-per-metre column went up.
# """


# def run_rop_check(validator, normalized_data, raise_alert, date_str, bit_depth, depth_unit,rop_unit, now):
#     rop_raw = normalized_data.get("ROP")
#     rop = validator._in_limit_unit("ROP", rop_raw)

#     rop_percentage = 0.0

#     if rop_raw is not None and rop is None:
#         # Not advancing - a connection or a trip. Nothing to compare, and
#         # the baseline goes with it so the next spell of drilling is
#         # measured from where it starts rather than from before the trip.
#         validator.previous_rop = None
#         validator.previous_rop_time = None
#         return rop_percentage

#     if rop is None:
#         return rop_percentage

#     current_time = now

#     # First value
#     if validator.previous_rop is None:
#         validator.previous_rop = rop
#         validator.previous_rop_time = current_time
#         return rop_percentage

#     # Prevent division by zero. ROP is legitimately 0 whenever the bit is
#     # not advancing (tripping, connections, circulating), so without this
#     # the next reading would divide by a zero baseline.
#     if validator.previous_rop <= 0:
#         validator.previous_rop = rop
#         validator.previous_rop_time = current_time
#         return rop_percentage

#     elapsed = (current_time - validator.previous_rop_time).total_seconds()

#     if elapsed < validator.rop_duration:
#         return rop_percentage

#     percent_change = ((rop - validator.previous_rop) / validator.previous_rop) * 100

#     validator.log.debug("ROP %s -> %s over %.1fs = %.2f%%",
#                          validator.previous_rop, rop, elapsed, percent_change)

    
#     if percent_change > validator.rop_threshold:
#         raise_alert(
#             f"[{date_str}] {validator.display_name('ROP')} increased by {percent_change:.2f}%({rop:.2f}{rop_unit}), BD:{bit_depth:.2f}{depth_unit}",
#             "ROP",
#             subject="ROP_CHANGE",
#             value=f"increased {percent_change:.2f}",
#             why=validator.alert_log.change_reason(
#                 "ROP", validator.previous_rop, rop, elapsed,
#                 percent_change, validator.rop_threshold,
#                 validator.rop_duration, raw=rop_raw,
#             ),
#         )
#     else:
#         validator._last_alerted.pop("ROP_CHANGE", None)

#     validator.previous_rop = rop
#     validator.previous_rop_time = current_time

#     rop_percentage = round(percent_change, 2)

#     return rop_percentage





from collections import deque

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

    # Initialize history if not already present
    if not hasattr(validator, "rop_history"):
        validator.rop_history = deque()

    # Add current reading
    validator.rop_history.append((current_time, rop))

    validator.log.info(
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

    validator.log.info(
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
                f"Current ROP {rop:.2f}{rop_unit} exceeded "
                f"average ROP {avg_rop:.2f}{rop_unit} by "
                f"{rop_percentage:.2f}% over the last "
                f"{validator.rop_duration} seconds."
            ),
        )
    else:
        validator._last_alerted.pop("ROP_CHANGE", None)

    return round(rop_percentage, 2)

