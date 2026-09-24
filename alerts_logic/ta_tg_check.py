"""
Check 3 - TA > TG.

Alert once TA has been above TG for TA_TG.duration_seconds.
"""


def run_ta_tg_check(validator, normalized_data, raise_alert, date_str, bit_depth, now):
    ta = normalized_data.get("TA")
    tg = normalized_data.get("TG")

    if ta is None or tg is None:
        return

    if ta > tg:
        if validator.ta_gt_tg_start is None:
            validator.ta_gt_tg_start = now
            validator.log.debug("TA>TG started (TA=%s TG=%s)", ta, tg)

        elapsed = (now - validator.ta_gt_tg_start).total_seconds()

        if elapsed >= validator.ta_tg_duration:
            raise_alert(
                f"[{date_str}] {validator.display_name('TA')} is greater than "
                f"{validator.display_name('TG')} where BD:{bit_depth}",
                "TA",
                "TG",
                subject="TA_TG",
                why=(
                    f"TA={ta} has been above TG={tg} for {elapsed:.0f}s, "
                    f"past conditions[TA_TG] of {validator.ta_tg_duration}s"
                ),
            )
    else:
        # Reset timer when condition clears
        if validator.ta_gt_tg_start is not None:
            validator.log.debug("TA>TG cleared")
        validator.ta_gt_tg_start = None