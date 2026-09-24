"""
alerts_logic
============

Each realtime QC check lives in its own module here, one per check, so
realtime_validator.py stays the orchestrator instead of growing every rule
inline. Every check function takes the RealtimeValidator instance as its
first argument (`validator`) and reads/writes its state exactly as the
original inline code did - nothing about the checks themselves changed,
only where the code lives:

    activity_check.py    - check 1: activity + the activity zero-checks
    range_check.py        - check 2: ranges (min/max)
    ta_tg_check.py         - check 3: TA > TG
    spp_check.py            - check 4: SPP vs SPM-derived factor
    rop_check.py             - check 6: ROP percentage change
    hookload_check.py         - check 7: HOOKLOAD stuck/unchanged
    bit_depth_check.py         - depth-jump check
    constants.py                 - shared constants + alert_raised_at
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