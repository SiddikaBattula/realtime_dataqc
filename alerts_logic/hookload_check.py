# """
# Check 7 - HOOKLOAD unchanged.

# A hookload that does not move at all is the sign of a stalled feed: the rig
# is still sending rows but the values in them are frozen. Alert when the
# value has not moved at all for HOOKLOAD.duration_seconds.
# """


# def run_hookload_check(validator, normalized_data, raise_alert, date_str, now):
#     hookload = normalized_data.get("HOOKLOAD")

#     if hookload is None:
#         return

#     current_time = now

#     # First reading
#     if validator.previous_hookload is None:
#         validator.previous_hookload = hookload
#         validator.previous_hookload_time = current_time
#         return

#     if hookload == validator.previous_hookload:

#         elapsed = (current_time - validator.previous_hookload_time).total_seconds()

#         if elapsed >= validator.hookload_duration:
#             raise_alert(
#                 f"[{date_str}] Please check for data Trans. {validator.display_name('HOOKLOAD')} has remained unchanged for {int(elapsed)} seconds",
#                 "HOOKLOAD",
#                 subject="HOOKLOAD_STUCK",
#                 value=current_time.timestamp(),
#                 why=validator.alert_log.stuck_reason(
#                     "HOOKLOAD", hookload, elapsed, validator.hookload_duration,
#                 ),
#             )
#             # Reset the timer so the alert does not fire every second
#             # for as long as the value stays stuck.
#             validator.previous_hookload_time = current_time

#     else:
#         # Value moved -> start measuring again from here.
#         validator.previous_hookload = hookload
#         validator.previous_hookload_time = current_time

#         validator._last_alerted.pop("HOOKLOAD_STUCK", None)


















"""
Check 7 - HOOKLOAD unchanged.

A hookload that does not move at all is the sign of a stalled feed: the rig
is still sending rows but the values in them are frozen.

Life of one alert:
  RAISED    value unchanged for HOOKLOAD.duration_seconds -> ONE new alert.
  STANDING  while it stays stuck, the alert is reported as standing every
            reading (so the log/dashboard count stays right) but is NOT sent
            as a new alert again.
  REMINDER  still stuck REALERT_SECONDS (30 min) after the last alert ->
            one new alert, then silent for another 30 min.
  RESOLVED  value moves again -> one "alert resolved" message + a log line
            with the stuck value, how long it was stuck and the new value.
"""

REALERT_SECONDS = 30 * 60


def run_hookload_check(validator, normalized_data, raise_alert, date_str, now):
    hookload = normalized_data.get("HOOKLOAD")

    if hookload is None:
        return

    name = validator.display_name("HOOKLOAD")

    # First reading
    if validator.previous_hookload is None:
        validator.previous_hookload = hookload
        validator.previous_hookload_time = now
        return

    stuck_value = validator.previous_hookload
    stuck_since = validator.previous_hookload_time
    elapsed = (now - stuck_since).total_seconds()

    # ------------------------------------------------------------------
    # Value moved -> resolved (if an alert was up) and start measuring again
    # ------------------------------------------------------------------
    if hookload != stuck_value:

        if validator.hookload_alert_active:
            stuck_for = int(elapsed)

            validator.log.info(
                "HOOKLOAD_STUCK RESOLVED | was stuck at %s for %ss (since %s) | "
                "now %s | resolved at %s",
                stuck_value, stuck_for, stuck_since.strftime("%H:%M:%S"),
                hookload, now.strftime("%H:%M:%S"),
            )

            raise_alert(
                f"[{date_str}] {name} alert resolved - value is changing again "
                f"(was stuck at {stuck_value} for {stuck_for} seconds, now {hookload})",
                "HOOKLOAD",
                subject="HOOKLOAD_RESOLVED",
                value=now.timestamp(),
                why=f"HOOKLOAD moved from {stuck_value} to {hookload} after "
                    f"{stuck_for}s unchanged",
            )

        validator.previous_hookload = hookload
        validator.previous_hookload_time = now
        validator.hookload_alert_active = False
        validator.hookload_last_alert_time = None
        validator.hookload_alert_token = None
        validator.hookload_alert_message = None
        validator._last_alerted.pop("HOOKLOAD_STUCK", None)
        return

    # ------------------------------------------------------------------
    # Value unchanged
    # ------------------------------------------------------------------
    why = validator.alert_log.stuck_reason(
        "HOOKLOAD", hookload, elapsed, validator.hookload_duration,
    )

    # Not alerting yet
    if not validator.hookload_alert_active:

        if elapsed < validator.hookload_duration:
            return

        message = (
            f"[{date_str}] Please check for data Trans. {name} has remained "
            f"unchanged at {hookload} for {int(elapsed)} seconds"
        )
        validator.hookload_alert_active = True
        validator.hookload_last_alert_time = now
        validator.hookload_alert_token = f"ACTIVE-{now.timestamp()}"
        validator.hookload_alert_message = message

        validator.log.warning(
            "HOOKLOAD_STUCK RAISED | value=%s | unchanged for %ss (limit %ss) | since %s",
            hookload, int(elapsed), validator.hookload_duration,
            stuck_since.strftime("%H:%M:%S"),
        )

        raise_alert(
            message, "HOOKLOAD",
            subject="HOOKLOAD_STUCK",
            value=validator.hookload_alert_token,
            why=why,
        )
        return

    # Alert already up: 30 minutes since the last one -> remind once
    since_alert = (now - validator.hookload_last_alert_time).total_seconds()

    if since_alert >= REALERT_SECONDS:
        message = (
            f"[{date_str}] Please check for data Trans. {name} is STILL unchanged "
            f"at {hookload} - stuck for {int(elapsed // 60)} minutes"
        )
        validator.hookload_last_alert_time = now
        validator.hookload_alert_token = f"ACTIVE-{now.timestamp()}"   # new token = new alert
        validator.hookload_alert_message = message

        validator.log.warning(
            "HOOKLOAD_STUCK REMINDER | value=%s | still stuck after %d min",
            hookload, int(elapsed // 60),
        )
    else:
        message = validator.hookload_alert_message

    # Same token as before -> counted as standing, NOT sent as a new alert.
    raise_alert(
        message, "HOOKLOAD",
        subject="HOOKLOAD_STUCK",
        value=validator.hookload_alert_token,
        why=why,
    )