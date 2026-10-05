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







"""
Check 6 - ROP.

Compares the current ROP with the AVERAGE of the previous readings, increase
only - a drop to zero is normal whenever the bit comes off bottom. Measured on
the reading after `factor`, so a rise means the bit is drilling faster and not
that the raw minutes-per-metre column went up.

    previous readings  10, 12, 11, 13      avg = 11.5
    limit = avg + ROP_value                (no percentage involved)
          = 11.5 + 11.5 = 23               (ROP_value = 11.5)

    current ROP = 20   ->  20 > 23 ?  no  -> no alert
    current ROP = 30   ->  30 > 23 ?  yes -> alert

The "previous readings" are every reading of the last `average_seconds`
(a rolling window, default 30 s), NOT just the last one - with a reading every
5 s that is the last six or so. Every new reading is compared with the average
of the ones before it. One alert is raised when ROP goes above the limit and
no more while it stays above; it can alert again once it has come back down.

Settings, in the ROP block of the well's conditions:

    "ROP": {
        "duration_seconds": 5,
        "percentage_change": 20,    (also used for the automatic ROP max)
        "value": 11.5,              the number added to the average
        "average_seconds": 30       how far back the average looks (optional)
    }

If "value" is missing the average itself is added (limit = avg + avg).
"""

DEFAULT_AVERAGE_SECONDS = 30.0

# Fewer previous readings than this is not an average yet.
MIN_SAMPLES = 2


def _setting(validator, key):
    block = validator.conditions.get("ROP")
    raw = block.get(key) if isinstance(block, dict) else None

    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def run_rop_check(validator, normalized_data, raise_alert, date_str, bit_depth, depth_unit, rop_unit, now):
    rop_raw = normalized_data.get("ROP")
    rop = validator._in_limit_unit("ROP", rop_raw)

    rop_percentage = 0.0

    if rop_raw is None:
        return rop_percentage

    # Not advancing (a connection, a trip, a stopped bit). Nothing to compare,
    # and the readings go with it so the next spell of drilling is measured
    # from where it starts rather than from before the stop.
    if rop is None or rop <= 0:
        validator.rop_history = []
        validator.previous_rop = None
        validator.previous_rop_time = None
        validator._last_alerted.pop("ROP_CHANGE", None)
        return rop_percentage

    # reset_state() clears previous_rop_time - the history goes with it.
    if validator.previous_rop_time is None:
        validator.rop_history = []

    window = _setting(validator, "average_seconds")

    if window is None or window <= 0:
        window = DEFAULT_AVERAGE_SECONDS

    # The previous readings: everything inside the window, current one excluded
    history = [
        (taken, value)
        for taken, value in getattr(validator, "rop_history", [])
        if (now - taken).total_seconds() <= window
    ]
    previous = [value for _, value in history]

    # The current reading becomes a "previous" one for the next comparison
    validator.rop_history = history + [(now, rop)]
    validator.previous_rop = rop
    validator.previous_rop_time = now

    if len(previous) < MIN_SAMPLES:
        return rop_percentage

    average = sum(previous) / len(previous)

    rop_value = _setting(validator, "value")

    if rop_value is None:
        rop_value = average

    limit = average + rop_value
    percent_change = ((rop - average) / average) * 100
    span = (now - history[0][0]).total_seconds()

    validator.log.warning(
        "ROP avg of %d readings %s = %.4f + value %.4f = limit %.4f | current %.4f (%.2f%%)",
        len(previous), [round(v, 2) for v in previous],
        average, rop_value, limit, rop, percent_change,
    )

    if rop > limit:
        raise_alert(
            f"[{date_str}] {validator.display_name('ROP')} increased by {percent_change:.2f}%({rop:.2f}{rop_unit}), BD:{bit_depth:.2f}{depth_unit}",
            "ROP",
            subject="ROP_CHANGE",
            value="increased",      # one alert per rise, not one per reading
            why=validator.alert_log.change_reason(
                "ROP", average, rop, span,
                percent_change, validator.rop_threshold,
                validator.rop_duration, raw=rop_raw,
            ),
        )
    else:
        validator._last_alerted.pop("ROP_CHANGE", None)

    return round(percent_change, 2)