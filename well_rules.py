"""
Each well's own rules, on disk.

The four files in data/ used to be the rules: every well was checked against
the same ranges, the same activity flags, the same column mapping. That only
holds while every rig is the same rig. A well now carries its own copy of all
four, entered in the dashboard when the well is added, and nothing it contains
affects any other well.

One file per well, in data/wells/:

    {
        "database_name": "kj-16",
        "ip_address": "10.0.0.5",
        "rules": {
            "activity":       {...},
            "column_mapping": {...},
            "conditions":     {...},
            "ranges":         {...}
        }
    }

data/*.json stay where they are and keep their meaning, but it is now the
template a new well starts from rather than the rules anything runs on. That
is what makes the form fillable: it opens with sensible values and you change
the handful that differ for this rig.

Files are written atomically, because a well's agent may be reading its rules
at the moment they are saved.
"""

import hashlib
import json
import os
import re

import rule_files
from config import Config
from logger import get_logger
from rule_files import RuleFileError

log = get_logger(__name__)

WELLS_DIR = Config.DATA_DIR / "wells"

# A database name is free text that has to become a filename. Anything outside
# this set is replaced, and the real name is kept inside the file - so two
# wells whose names differ only in punctuation cannot collide on one file.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _filename(database_name):
    safe = _UNSAFE.sub("_", database_name).strip("._-") or "well"

    # Names that differ only in what was replaced would otherwise share a file,
    # so the suffix keeps them apart. It has to be a stable digest and not
    # hash(): that is salted per process, so the same well would land on a
    # different filename after every restart and lose its rules.
    if safe != database_name:
        digest = hashlib.sha1(database_name.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe}-{digest}"

    return safe + ".json"


def path_for(database_name):
    return WELLS_DIR / _filename(database_name)


def template():
    """What a new well's form opens with: the four files in data/."""
    return rule_files.template()


def load(database_name):
    """One well's saved record, or None if it has never been saved."""
    path = path_for(database_name)

    if not path.exists():
        return None

    return _read(path)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)

    except (OSError, json.JSONDecodeError) as exc:
        raise RuleFileError(f"{path.name} could not be read: {exc}") from exc

    if not isinstance(record, dict) or "rules" not in record:
        raise RuleFileError(f"{path.name} is not a well record")

    return record


def load_rules(path):
    """Just the rules out of a well file, given its path.

    The agent's validator uses this to pick up an edit without a restart, so
    it takes a path rather than a name - it already knows which file is its.
    """
    return _read(path)["rules"]


def save(database_name, ip_address, rules):
    """
    Check the rules, then write the well's record.

    Returns the record as it was stored - numbers tidied and duplicate column
    names dropped, so the caller can echo back what is actually on disk rather
    than what was typed.
    """
    if not database_name or not str(database_name).strip():
        raise RuleFileError("The well needs a database name")

    if not ip_address or not str(ip_address).strip():
        raise RuleFileError("The well needs an IP address")

    # Tidy first so validation sees the numbers as they will be stored, then
    # check. tidy_set copes with an incomplete set; validate_set is what says
    # which block is missing.
    rules = rule_files.tidy_set(rules)

    rule_files.validate_set(rules)

    record = {
        "database_name": database_name,
        "ip_address": ip_address,
        "rules": rules,
    }

    WELLS_DIR.mkdir(parents=True, exist_ok=True)

    path = path_for(database_name)

    # Written beside the real file and moved into place: the well's agent may
    # read this at any moment and must never catch it half-written.
    temporary = path.with_suffix(".json.writing")

    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())

    os.replace(temporary, path)

    log.info("Saved rules for %s (%s)", database_name, path.name)

    return record


def delete(database_name):
    """Remove a well's file. True if there was one."""
    path = path_for(database_name)

    try:
        path.unlink()
        log.info("Removed rules for %s", database_name)
        return True

    except FileNotFoundError:
        return False

    except OSError as exc:
        raise RuleFileError(f"{path.name} could not be removed: {exc}") from exc


def all_records():
    """
    Every well saved on disk, so monitoring resumes after a restart.

    A file that cannot be read is reported and skipped - one bad well should
    not stop the others coming back.
    """
    if not WELLS_DIR.is_dir():
        return []

    records = []

    for path in sorted(WELLS_DIR.glob("*.json")):
        try:
            records.append(_read(path))

        except RuleFileError as exc:
            log.error("Skipping %s: %s", path.name, exc)

    return records
