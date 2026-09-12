"""
The wells being monitored, as the API and the agent manager share them.

Two threads touch this: the config API adds and removes wells, the agent
manager walks the list every few seconds to decide which agents to start and
stop. Iterating a plain dict while another thread writes to it raises
"dictionary changed size during iteration", so every access goes through the
lock below and hands back a copy.

A well is its connection details plus its own four rule blocks, and the whole
thing is written to data/wells/ - so unlike before, monitoring resumes by
itself after a restart instead of every well having to be added again.
"""

import threading

import well_rules
from logger import get_logger

log = get_logger(__name__)

_LOCK = threading.RLock()

# database_name -> {"database_name", "ip_address", "rules"}
_WELLS = {}


def load_saved():
    """Bring back every well on disk. Returns how many."""
    with _LOCK:
        for record in well_rules.all_records():
            _WELLS[record["database_name"]] = record

        if _WELLS:
            log.info("Resumed %d well(s) from %s", len(_WELLS), well_rules.WELLS_DIR)

        return len(_WELLS)


def add(database_name, ip_address, rules):
    """
    Save a well and put it in the registry.

    The write happens first: a well the agent picks up but that is not on disk
    would vanish at the next restart with no sign of why.
    """
    record = well_rules.save(database_name, ip_address, rules)

    with _LOCK:
        _WELLS[database_name] = record

    return record


def remove(database_name):
    """Drop a well and its file. True if it was there."""
    with _LOCK:
        existed = _WELLS.pop(database_name, None) is not None

    well_rules.delete(database_name)

    return existed


def get(database_name):
    with _LOCK:
        return _WELLS.get(database_name)


def all_wells():
    """A snapshot - safe to iterate while another thread is editing."""
    with _LOCK:
        return dict(_WELLS)


def summaries():
    """
    Every well without its rules.

    The dashboard polls this every couple of seconds and only needs the name
    and address; the rule blocks are large and are fetched on demand instead.
    """
    with _LOCK:
        return {
            name: {
                "database_name": record["database_name"],
                "ip_address": record["ip_address"],
            }
            for name, record in _WELLS.items()
        }


def count():
    with _LOCK:
        return len(_WELLS)
