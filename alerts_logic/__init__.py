"""
alerts_logic
============

Each realtime QC check lives in its own module here, one per check, so
validation_realtime.py stays the orchestrator instead of growing every rule
inline. Every check function takes the RealtimeValidator instance as its
first argument (`validator`) and reads/writes its state on it:

    activity_check.py    - check 1: activity, the zero-checks, and "resumed"
    range_check.py       - check 2: ranges (min/max)
    ta_tg_check.py       - check 3: TA > TG
    spp_check.py         - check 4: SPP against SPM x the learned ratio
    rop_check.py         - check 5: ROP above its rolling average
    hookload_check.py    - check 6: HOOKLOAD stuck/unchanged
    bit_depth_check.py   - check 7: bit depth jumps, and "steady" again
    constants.py         - shared constants, alert_raised_at, is_duration_only
"""

from .activity_check import detect_activity, run_zero_checks
from .range_check import run_range_checks
from .ta_tg_check import run_ta_tg_check
from .spp_check import run_spp_check
from .rop_check import run_rop_check
from .hookload_check import run_hookload_check
from .bit_depth_check import run_bit_depth_check
from .constants import (
    ALERT_TIME_FORMAT,
    EVENT_SUBJECTS,
    INVERSE_PARAMS,
    PUMPS,
    DERIVED_PARAMS,
    OPTIONAL_PARAMS,
    alert_raised_at,
)

__all__ = [
    "detect_activity",
    "run_zero_checks",
    "run_range_checks",
    "run_ta_tg_check",
    "run_spp_check",
    "run_rop_check",
    "run_hookload_check",
    "run_bit_depth_check",
    "ALERT_TIME_FORMAT",
    "EVENT_SUBJECTS",
    "INVERSE_PARAMS",
    "PUMPS",
    "DERIVED_PARAMS",
    "OPTIONAL_PARAMS",
    "alert_raised_at",
]