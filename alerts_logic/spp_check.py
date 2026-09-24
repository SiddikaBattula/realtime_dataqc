"""
Check 4 - SPP.

Compares the current SPP against an SPP/SPM ratio ("factor") learned from
the well itself and refreshed every spp_factor_duration seconds, and alerts
when SPP sits outside spp_threshold percent of what that factor predicts.
"""

from datetime import datetime


def run_spp_check(validator, normalized_data,data_str, raise_alert):
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
        return

    current_time = datetime.now()
    input_factor = validator.spp_threshold / 100.0

    factor_elapsed = (current_time - validator.spp_spm_factor_time).total_seconds()

    if factor_elapsed >= validator.spp_factor_duration:
        validator.spp_spm_factor = curr_spp / curr_spm
        validator.spp_spm_factor_time = current_time

    if validator.spp_spm_factor <= 0:
        return

    comparison_elapsed = (current_time - validator.spp_comparison_time).total_seconds()

    if comparison_elapsed < 5:
        return

    # Reset comparison timer
    validator.spp_comparison_time = current_time
    calculated_spp = curr_spm * validator.spp_spm_factor

    upper_limit = calculated_spp + (calculated_spp * input_factor)
    lower_limit = calculated_spp - (calculated_spp * input_factor)

    validator.log.warning(
        "COMPARE | Factor=%.4f | Calculated SPP=%.4f | "
        "Current SPP=%.4f | Upper=%.4f | Lower=%.4f",
        validator.spp_spm_factor,
        calculated_spp,
        curr_spp,
        upper_limit,
        lower_limit,
    )

    if curr_spp > upper_limit:

        raise_alert(
            f"[{data_str}] SPP is out of expected range",
            subject="SPP_SPM_FACTOR",
            value="HIGH",
        )

    elif curr_spp < lower_limit:

        raise_alert(
            f"[{data_str}] SPP is out of expected range",
            subject="SPP_SPM_FACTOR",
            value="LOW",
        )

    else:
        validator.log.warning(
            "SPP OK | Current=%.2f Expected=%.2f",
            curr_spp,
            calculated_spp,
        )