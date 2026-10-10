# """
# Check 4 - SPP.

# Compares the current SPP against an SPP/SPM ratio ("factor") learned from
# the well itself and refreshed every spp_factor_duration seconds, and alerts
# when SPP sits outside spp_threshold percent of what that factor predicts.
# """

# from datetime import datetime


# def run_spp_check(validator, normalized_data,data_str, raise_alert,spp_unit,spm_unit):
#     curr_spm = validator._get_total_spm(normalized_data)

#     curr_spp = validator._apply_factor(
#         "SPP",
#         normalized_data.get("SPP"),
#         validator.ranges.get("SPP", {}).get("factor"),
#     )

#     curr_spm = validator._apply_factor(
#         "SPM",
#         curr_spm,
#         validator.ranges.get("SPM", {}).get("factor"),
#     )

#     validator.log.warning(
#         "DEBUG 1 | SPP=%s | SPM=%s | MP1=%s | MP2=%s | MP3=%s | MP4=%s",
#         curr_spp,
#         curr_spm,
#         normalized_data.get("MP1_SPM"),
#         normalized_data.get("MP2_SPM"),
#         normalized_data.get("MP3_SPM"),
#         normalized_data.get("MP4_SPM"),
#     )

#     if not (curr_spp is not None and curr_spm is not None and curr_spm > 0):
#         return

#     current_time = datetime.now()
#     input_factor = validator.spp_threshold / 100.0

#     factor_elapsed = (current_time - validator.spp_spm_factor_time).total_seconds()

#     if factor_elapsed >= validator.spp_factor_duration:
#         validator.spp_spm_factor = curr_spp / curr_spm
#         validator.spp_spm_factor_time = current_time

#     if validator.spp_spm_factor <= 0:
#         return

#     comparison_elapsed = (current_time - validator.spp_comparison_time).total_seconds()

#     if comparison_elapsed < 5:
#         return

#     # Reset comparison timer
#     validator.spp_comparison_time = current_time
#     calculated_spp = curr_spm * validator.spp_spm_factor

#     upper_limit = calculated_spp + (calculated_spp * input_factor)
#     lower_limit = calculated_spp - (calculated_spp * input_factor)

#     validator.log.warning(
#         "COMPARE | Factor=%.4f | Calculated SPP=%.4f | "
#         "Current SPP=%.4f | Upper=%.4f | Lower=%.4f",
#         validator.spp_spm_factor,
#         calculated_spp,
#         curr_spp,
#         upper_limit,
#         lower_limit,
#     )

#     if curr_spp > upper_limit:

#         # raise_alert(
#         #     f"[{data_str}] upper limit:{upper_limit:.2f} | current value:{curr_spp:.2f}{spp_unit} current spm:{curr_spm:.2f}{spm_unit} | lower limit:{lower_limit:.2f} SPP is out of expected range",
#         #     subject="SPP_SPM_FACTOR",
#         #     value="HIGH",
#         # )

#         raise_alert(
#             f"[{data_str}] SPP out of range.",
#             subject="SPP_SPM_FACTOR",
#             value="HIGH",
#         )

#     elif curr_spp < lower_limit:

#         # raise_alert(
#         #     f"[{data_str}] upper limit:{upper_limit:.2f} | current value:{curr_spp:.2f}{spp_unit} current spm:{curr_spm:.2f}{spm_unit} | lower limit:{lower_limit:.2f} SPP is out of expected range",
#         #     subject="SPP_SPM_FACTOR",
#         #     value="LOW",
#         # )

#         raise_alert(
#             f"[{data_str}] SPP out of range.",
#             subject="SPP_SPM_FACTOR",
#             value="HIGH",
#         )

#     else:
#         validator.log.warning(
#             "SPP OK | Current=%.2f Expected=%.2f",
#             curr_spp,
#             calculated_spp,
#         )








"""
Check 4 - SPP.

Compares the current SPP against an SPP/SPM ratio ("factor") learned from
the well itself and refreshed every spp_factor_duration seconds, and alerts
when SPP sits outside spp_threshold percent of what that factor predicts.

The alert says how long SPP has been out of range ("SPP out of range from
60s"). The clock starts on the first out-of-range comparison and keeps
running while SPP stays out of range on the same side.

It is NOT reset by a single in-range comparison. SPP hovering around the
threshold, a pump blip, or the factor being re-learned (which makes that one
comparison match exactly) would otherwise throw the count back to 0s every
time. The clock is only dropped once SPP has been back in range - or the pump
has been off - for SPP_CLEAR_AFTER_SECONDS in a row, or SPP crosses to the
other side.
"""

import time
from datetime import datetime

# Same timer helpers the range check uses, so both alerts behave identically
# (including ROUND_UP_TO_SECONDS). They only know the directions "above" and
# "below". The key below is kept apart from any parameter name in
# ranges.json so the two timers can never collide.
from .range_check import _clear_violation, _violation_seconds

SPP_TIMER_KEY = "SPP_SPM_FACTOR"

# How long SPP must stay back in range (or the pump stay off) before the
# "from Ns" count starts over. Raise it to be more forgiving of flicker.
SPP_CLEAR_AFTER_SECONDS = 30


def _mark_out_of_range(validator):
    validator.__dict__["_spp_last_out"] = time.time()


def _clear_if_settled(validator, why):
    """Reset the clock only after SPP has been fine for a while."""
    last_out = validator.__dict__.get("_spp_last_out")

    if last_out is None:
        return

    quiet = time.time() - last_out

    if quiet >= SPP_CLEAR_AFTER_SECONDS:
        _clear_violation(validator, SPP_TIMER_KEY)
        validator.__dict__["_spp_last_out"] = None
        validator.log.warning("SPP timer reset | %s for %.0fs", why, quiet)
    else:
        validator.log.warning(
            "SPP timer kept | %s for only %.0fs of %ds", why, quiet, SPP_CLEAR_AFTER_SECONDS,
        )


def run_spp_check(validator, normalized_data, data_str, raise_alert, spp_unit, spm_unit):
    curr_spm = validator._get_total_spm(normalized_data)

    curr_spp = validator._apply_factor(
        "SPP",
        normalized_data.get("SPP"),
        validator.ranges.get("SPP", {}).get("factor"),
    )

    curr_spm = validator._apply_factor(
        "SPM",
        curr_spm,
        validator.ranges.get("SPM", {}).get("factor"),
    )

    validator.log.warning(
        "DEBUG 1 | SPP=%s | SPM=%s | MP1=%s | MP2=%s | MP3=%s | MP4=%s",
        curr_spp,
        curr_spm,
        normalized_data.get("MP1_SPM"),
        normalized_data.get("MP2_SPM"),
        normalized_data.get("MP3_SPM"),
        normalized_data.get("MP4_SPM"),
    )

    if not (curr_spp is not None and curr_spm is not None and curr_spm > 0):
        # Pump off / no reading: nothing is being measured. A short blip keeps
        # the clock; a real stop lets it start over.
        _clear_if_settled(validator, "pump off / no reading")
        return

    current_time = datetime.now()
    input_factor = validator.spp_threshold / 100.0

    factor_elapsed = (current_time - validator.spp_spm_factor_time).total_seconds()

    if factor_elapsed >= validator.spp_factor_duration:
        validator.spp_spm_factor = curr_spp / curr_spm
        validator.spp_spm_factor_time = current_time

        validator.log.warning(
            "SPP factor re-learned = %.4f - comparison skipped this pass",
            validator.spp_spm_factor,
        )

        # The factor was just built from this very reading, so comparing
        # against it would always say "in range". Skip, and leave the clock
        # alone.
        return

    if validator.spp_spm_factor <= 0:
        return

    # Compared every 5 s. Returning here does NOT touch the timer: nothing
    # was compared, so nothing has changed.
    comparison_elapsed = (current_time - validator.spp_comparison_time).total_seconds()

    if comparison_elapsed < 5:
        return

    # Reset comparison timer
    validator.spp_comparison_time = current_time
    calculated_spp = curr_spm * validator.spp_spm_factor

    upper_limit = calculated_spp + (calculated_spp * input_factor)
    lower_limit = calculated_spp - (calculated_spp * input_factor)

    validator.log.info(
        "COMPARE | Factor=%.4f | Calculated SPP=%.4f | "
        "Current SPP=%.4f | Upper=%.4f | Lower=%.4f",
        validator.spp_spm_factor,
        calculated_spp,
        curr_spp,
        upper_limit,
        lower_limit,
    )

    if curr_spp > upper_limit:
        # Crossing from the other side is a different alert, so that side's
        # clock is dropped. This side keeps its start time.
        _clear_violation(validator, SPP_TIMER_KEY, "below")
        secs = _violation_seconds(validator, SPP_TIMER_KEY, "above")
        _mark_out_of_range(validator)

        raise_alert(
            f"[{data_str}] SPP out of range from {secs}s",
            subject="SPP_SPM_FACTOR",
            value="HIGH",
        )

    elif curr_spp < lower_limit:
        _clear_violation(validator, SPP_TIMER_KEY, "above")
        secs = _violation_seconds(validator, SPP_TIMER_KEY, "below")
        _mark_out_of_range(validator)

        raise_alert(
            f"[{data_str}] SPP out of range from {secs}s",
            subject="SPP_SPM_FACTOR",
            value="LOW",     # was "HIGH" - a copy-paste slip in the low branch
        )

    else:
        validator.log.warning(
            "SPP OK | Current=%.2f Expected=%.2f",
            curr_spp,
            calculated_spp,
        )

        # In range now, but one good reading is not "back to normal".
        _clear_if_settled(validator, "in range")