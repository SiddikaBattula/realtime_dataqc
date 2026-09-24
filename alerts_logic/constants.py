"""
Shared constants and small helpers used across the alert checks.

Keeping these in one place means every check module agrees on what an
"event" subject is, which parameters are inverse, which pump columns exist,
and so on - instead of each file having its own copy that can drift.
"""

import re
from datetime import datetime

# The timestamp every alert starts with, "[17-09-26 05-32-43]". The alerts
# endpoint and the agent read it back to tell how old an alert is.
ALERT_TIME_FORMAT = "%d-%m-%y %H-%M-%S"

_ALERT_STAMP = re.compile(r"^\[([^\]]*)\]")

# Subjects raised by checks that only look once per window (or, for HOOKLOAD,
# once per stall period). A reading in between says nothing about them, so
# they are not cleared just for not being raised - their own check clears them
# when it looks and finds nothing.
EVENT_SUBJECTS = {"SPP_CHANGE", "SPM_CHANGE", "ROP_CHANGE", "HOOKLOAD_STUCK"}

# Parameters whose column holds the reciprocal of the unit their limits are
# written in, so `factor` divides the reading instead of multiplying it.
#
# ROP is the only one: the rig stores minutes per metre and ranges is written
# in metres per hour, and 60 / 0.5 min/m = 120 m/hr. Every other parameter is
# a straight multiply - HOOKLOAD's 2.268 turns daN into klbf.
INVERSE_PARAMS = {"ROP"}

# The pump columns SPM is totalled from. A rig with three pumps simply has no
# column for the other two, and they are skipped.
PUMPS = ("MP1_SPM", "MP2_SPM", "MP3_SPM", "MP4_SPM", "MP5_SPM")

# Logical names that are worked out from other columns instead of being read
# from one of their own. SPM is the only one - see realtime_validator.py for
# the full explanation of why.
DERIVED_PARAMS = {"SPM"}

# Absent columns that are not a mistake - see realtime_validator.py.
OPTIONAL_PARAMS = set(PUMPS)


def alert_raised_at(alert):
    """When an alert was raised, from its "[17-09-26 05-32-43]" prefix, or None."""
    match = _ALERT_STAMP.match(alert) if isinstance(alert, str) else None

    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), ALERT_TIME_FORMAT)
    except ValueError:
        return None