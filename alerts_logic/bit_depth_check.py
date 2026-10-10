"""
Depth-jump check.

bit_depth is the one read at the top of the reading - nothing between there
and here can have changed it. If it moves by more than bit_depth_threshold
between two consecutive readings, that is flagged.

A flag remembers when the jumping started. A jump is one reading, not a state,
and a jumpy rig raises one every few seconds - so the jumping has stopped once
there has been none for STEADY_AFTER_SECONDS, and then one "steady again"
alert says from when to when it was jumping.
"""

from datetime import datetime

from .constants import ALERT_TIME_FORMAT

# Seconds without a jump before the bit depth counts as steady again.
STEADY_AFTER_SECONDS = 30

# The from/to times inside the "steady again" alert. ":" rather than the
# stamp's "-", so they cannot be mistaken for the "[...]" every alert starts with.
SPAN_TIME_FORMAT = "%d-%m-%y %H:%M:%S"


def run_bit_depth_check(validator, bit_depth, raise_alert, date_str, depth_unit,threshold):
    now = datetime.strptime(date_str, ALERT_TIME_FORMAT)
    jumped = False

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
                    f"last depth : {validator.last_bit_depth:.2f} | "
                    f"current depth : {bit_depth:.2f} | "
                    f"Bit Depth jump by {difference:.2f}{depth_unit} "
                ),
                "BIT_DPT_MD",
                subject="BIT_DEPTH_CHANGE",
                value=round(difference, 2),
            )
            jumped = True
            _note_jump(validator, now, difference)

    if not jumped:
        _report_steady(validator, now, raise_alert, date_str, bit_depth, depth_unit)

    validator.last_bit_depth = bit_depth


def _note_jump(validator, now, difference):
    """Raise the flag on the first jump, and keep count while it stays up."""
    state = validator.__dict__

    if state.get("_jump_since") is None:
        state["_jump_since"] = now
        state["_jump_count"] = 0
        state["_jump_largest"] = 0.0

    state["_jump_last"] = now
    state["_jump_count"] += 1
    state["_jump_largest"] = max(state["_jump_largest"], difference)


def _report_steady(validator, now, raise_alert, date_str, bit_depth, depth_unit):
    """The flag is up and no jump for STEADY_AFTER_SECONDS: say so, drop it."""
    state = validator.__dict__
    since = state.get("_jump_since")

    if since is None:
        return

    last = state["_jump_last"]

    if (now - last).total_seconds() < STEADY_AFTER_SECONDS:
        return

    seconds = int((last - since).total_seconds())
    count = state["_jump_count"]
    largest = state["_jump_largest"]

    state["_jump_since"] = None

    validator.log.info(
        "BIT_DEPTH_CHANGE STEADY | jumping from %s to %s (%ss), %d jump(s), largest %.2f",
        since.strftime("%H:%M:%S"), last.strftime("%H:%M:%S"), seconds, count, largest,
    )

    where = f", BD:{bit_depth:.2f}{depth_unit}" if bit_depth is not None else ""

    raise_alert(
        f"[{date_str}] Bit depth steady again since {last.strftime('%H:%M:%S')} - "
        f"was jumping from {since.strftime(SPAN_TIME_FORMAT)} "
        f"to {last.strftime(SPAN_TIME_FORMAT)} ({seconds}s), "
        f"{count} jump(s), largest {largest:.2f}{depth_unit}{where}",
        "BIT_DPT_MD",
        subject="BIT_DEPTH_STEADY",
        value=since.timestamp(),
        why=f"no bit depth jump above the threshold for {STEADY_AFTER_SECONDS}s",
    )
