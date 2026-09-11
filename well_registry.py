"""
The wells being monitored, as the API and the agent manager share them.

Two threads touch this: the config API adds and removes wells, the agent
manager walks the list every few seconds to decide which agents to start and
stop. Iterating a plain dict while another thread writes to it raises
"dictionary changed size during iteration", so every access goes through the
lock below and hands back a copy.

The registry is in memory only - wells added over the API are gone on restart.
"""

import threading

_LOCK = threading.RLock()

# database_name -> {"database_name": str, "ip_address": str}
_WELLS = {}


def add(database_name, ip_address):
    """Add or update a well. Returns the stored entry."""
    entry = {"database_name": database_name, "ip_address": ip_address}

    with _LOCK:
        _WELLS[database_name] = entry

    return entry


def remove(database_name):
    """Drop a well. Returns True if it was there."""
    with _LOCK:
        return _WELLS.pop(database_name, None) is not None


def get(database_name):
    with _LOCK:
        return _WELLS.get(database_name)


def all_wells():
    """A snapshot - safe to iterate while another thread is editing."""
    with _LOCK:
        return dict(_WELLS)


def count():
    with _LOCK:
        return len(_WELLS)
