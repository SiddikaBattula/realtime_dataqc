"""
Depth-jump check.

bit_depth is the one read at the top of the reading - nothing between there
and here can have changed it. If it moves by more than bit_depth_threshold
between two consecutive readings, that is flagged.
"""


def run_bit_depth_check(validator, bit_depth, raise_alert, date_str, depth_unit,threshold):
    if validator.last_bit_depth is not None:

        difference = abs(bit_depth - validator.last_bit_depth)

        validator.log.warning(
            "BIT DEPTH CHECK |current=%s | Previous=%s | Threshold=%.2f",
            bit_depth,
            f"{validator.last_bit_depth:.2f}" if validator.last_bit_depth is not None else "None",
            threshold,
        )

        if difference > threshold:
            raise_alert(
                (
                    f"[{date_str}] "
                    f"last depth : {validator.last_bit_depth} | "
                    f"current depth : {bit_depth} | "
                    f"Bit Depth jump by {difference:.2f}{depth_unit} "
                ),
                "BIT_DPT_MD",
                subject="BIT_DEPTH_CHANGE",
                value=round(difference, 2),
            )

    validator.last_bit_depth = bit_depth