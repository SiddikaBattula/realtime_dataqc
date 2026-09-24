"""
Check 7 - HOOKLOAD unchanged.

A hookload that does not move at all is the sign of a stalled feed: the rig
is still sending rows but the values in them are frozen. Alert when the
value has not moved at all for HOOKLOAD.duration_seconds.
"""


def run_hookload_check(validator, normalized_data, raise_alert, date_str, now):
    hookload = normalized_data.get("HOOKLOAD")

    if hookload is None:
        return

    current_time = now

    # First reading
    if validator.previous_hookload is None:
        validator.previous_hookload = hookload
        validator.previous_hookload_time = current_time
        return

    if hookload == validator.previous_hookload:

        elapsed = (current_time - validator.previous_hookload_time).total_seconds()

        if elapsed >= validator.hookload_duration:
            raise_alert(
                f"[{date_str}] Please check for data Trans. {validator.display_name('HOOKLOAD')} has remained unchanged for {int(elapsed)} seconds",
                "HOOKLOAD",
                subject="HOOKLOAD_STUCK",
                why=validator.alert_log.stuck_reason(
                    "HOOKLOAD", hookload, elapsed, validator.hookload_duration,
                ),
            )
            # Reset the timer so the alert does not fire every second
            # for as long as the value stays stuck.
            validator.previous_hookload_time = current_time

    else:
        # Value moved -> start measuring again from here.
        validator.previous_hookload = hookload
        validator.previous_hookload_time = current_time

        validator._last_alerted.pop("HOOKLOAD_STUCK", None)