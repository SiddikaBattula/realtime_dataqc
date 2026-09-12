"""
Reading and writing the rule files, with the checks that make it safe.

Editing data/*.json by hand goes wrong in ways nothing notices until the agent
is already running on it: a trailing comma, "1" where 1 was meant, a parameter
no column maps to, a min above its max, a conditions block deleted that the
validator reads on startup. Every write that goes through here is checked
against the other rule files first, saved atomically, and the version it
replaced kept as .bak - so a bad edit is refused now instead of found later.

The files stay the single source of truth. Nothing here talks to the agent:
it notices the file changed and reloads by itself.
"""

from __future__ import annotations

import json
import os
import shutil

from config import Config
from logger import get_logger

log = get_logger(__name__)


class RuleFileError(Exception):
    """A rule file, or a proposed change to one, is not usable."""


# name used in the URL -> file on disk
FILES = {
    "activity": Config.ACTIVITY_FILE,
    "conditions": Config.CONDITIONS_FILE,
    "ranges": Config.RANGES_FILE,
    "column_mapping": Config.COLUMN_MAP_FILE,
}

# What the validator reads out of conditions.json when it starts. Deleting one
# of these is a KeyError at the next reload, so a write that drops one is
# refused here instead.
REQUIRED_CONDITIONS = {
    "TA_TG": ("duration_seconds",),
    "SPP": ("percentage_change", "duration_seconds"),
    "SPM": ("percentage_change", "duration_seconds"),
    "ROP": ("percentage_change", "duration_seconds"),
    "HOOKLOAD": ("duration_seconds",),
}


def path_for(name):
    if name not in FILES:
        raise RuleFileError(f"Unknown rule file '{name}'. Known: {', '.join(FILES)}")

    return FILES[name]


def load(name):
    """The current contents of a rule file."""
    path = path_for(name)

    if not path.exists():
        raise RuleFileError(f"{path.name} does not exist")

    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    except json.JSONDecodeError as exc:
        raise RuleFileError(f"{path.name} on disk is not valid JSON: {exc}") from exc


def changed_at(name):
    """When the file was last written, or None if it is not there."""
    path = path_for(name)

    try:
        return path.stat().st_mtime

    except OSError:
        return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _as_number(value, where):
    """A number, or a string that is one - ranges.json stores factor as "60"."""
    if isinstance(value, bool):
        raise RuleFileError(f"{where} must be a number, not true/false")

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass

    raise RuleFileError(f"{where} must be a number, got {value!r}")


def _known_parameters(pending_mapping=None):
    """Every logical name the rules are allowed to refer to."""
    mapping = pending_mapping if pending_mapping is not None else load("column_mapping")

    return set(mapping)


def _check_object(document, label):
    if not isinstance(document, dict) or not document:
        raise RuleFileError(f"{label} must be a non-empty JSON object")


def _check_activity(document, known):
    _check_object(document, "activity.json")

    for activity, rules in document.items():
        _check_object(rules, f"activity.json: '{activity}'")

        unknown = sorted(set(rules) - known)

        if unknown:
            raise RuleFileError(
                f"activity.json: '{activity}' refers to {', '.join(unknown)}, which "
                "have no entry in column_mapping.json - add the column name there first"
            )

        for param, flag in rules.items():
            if flag not in (0, 1) or isinstance(flag, bool):
                raise RuleFileError(
                    f"activity.json: '{activity}.{param}' must be 1 (required) or "
                    f"0 (ignored), got {flag!r}"
                )


def _check_ranges(document, known):
    _check_object(document, "ranges.json")

    for param, limits in document.items():
        _check_object(limits, f"ranges.json: '{param}'")

        if param not in known:
            raise RuleFileError(
                f"ranges.json: '{param}' has no entry in column_mapping.json - "
                "add the column name there first"
            )

        for required in ("min", "max"):
            if required not in limits:
                raise RuleFileError(f"ranges.json: '{param}' has no {required}")

        low = _as_number(limits["min"], f"ranges.json: '{param}.min'")
        high = _as_number(limits["max"], f"ranges.json: '{param}.max'")

        if low > high:
            raise RuleFileError(
                f"ranges.json: '{param}' has min {low} above max {high} - "
                "every reading would alert"
            )

        if "unit" in limits and not isinstance(limits["unit"], str):
            raise RuleFileError(f"ranges.json: '{param}.unit' must be text")

        if limits.get("factor") is not None:
            factor = _as_number(limits["factor"], f"ranges.json: '{param}.factor'")

            if factor == 0:
                raise RuleFileError(f"ranges.json: '{param}.factor' cannot be 0")

        extra = sorted(set(limits) - {"min", "max", "unit", "factor"})

        if extra:
            raise RuleFileError(
                f"ranges.json: '{param}' has unexpected field(s) {', '.join(extra)}. "
                "Allowed: min, max, unit, factor"
            )


def _check_conditions(document, known):
    _check_object(document, "conditions.json")

    for block, fields in document.items():
        _check_object(fields, f"conditions.json: '{block}'")

        for field, value in fields.items():
            number = _as_number(value, f"conditions.json: '{block}.{field}'")

            if field == "duration_seconds" and number < 0:
                raise RuleFileError(
                    f"conditions.json: '{block}.duration_seconds' cannot be negative"
                )

            if field == "percentage_change" and number <= 0:
                raise RuleFileError(
                    f"conditions.json: '{block}.percentage_change' must be above 0 - "
                    "at 0 every reading raises an alert"
                )

    for block, fields in REQUIRED_CONDITIONS.items():
        if block not in document:
            raise RuleFileError(
                f"conditions.json: the '{block}' block is required - the agent reads "
                "it when it starts and cannot run without it"
            )

        for field in fields:
            if field not in document[block]:
                raise RuleFileError(f"conditions.json: '{block}' has no {field}")


def _check_column_mapping(document, known):
    _check_object(document, "column_mapping.json")

    for logical, aliases in document.items():
        if not isinstance(aliases, list) or not aliases:
            raise RuleFileError(
                f"column_mapping.json: '{logical}' must be a non-empty list of "
                "column names, e.g. [\"TOT_DPT_MD\", \"DEPTH\"]"
            )

        if not all(isinstance(a, str) and a.strip() for a in aliases):
            raise RuleFileError(
                f"column_mapping.json: every name for '{logical}' must be text"
            )

        # A name repeated in a different case is not an error: matching against
        # the table ignores case, so the second one was never doing anything.
        # _dedupe_aliases() drops it on the way in rather than refusing the write.

    # Removing a logical name the rules still use would leave those checks
    # unrunnable, so the other two files get a say in this one.
    still_needed = set()

    for rule_file in ("ranges", "activity"):
        try:
            content = load(rule_file)
        except RuleFileError:
            continue

        if rule_file == "ranges":
            still_needed |= set(content)
        else:
            for rules in content.values():
                still_needed |= set(rules)

    dropped = sorted(still_needed - set(document))

    if dropped:
        raise RuleFileError(
            f"column_mapping.json: {', '.join(dropped)} would be removed, but "
            "ranges.json or activity.json still refer to them"
        )


CHECKS = {
    "activity": _check_activity,
    "conditions": _check_conditions,
    "ranges": _check_ranges,
    "column_mapping": _check_column_mapping,
}


def validate(name, document):
    """Raise RuleFileError if `document` is not a usable version of this file."""
    pending = document if name == "column_mapping" else None

    try:
        known = _known_parameters(pending)

    except RuleFileError as exc:
        raise RuleFileError(f"column_mapping.json must be readable first: {exc}") from exc

    CHECKS[name](document, known)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def merge(base, patch):
    """
    `patch` laid over `base`, one level into each block.

    So {"DRILLING": {"WOB": 0}} changes that one flag and leaves the other
    twenty-one alone. A value of null removes the key.
    """
    merged = dict(base)

    for key, value in patch.items():

        if value is None:
            merged.pop(key, None)
            continue

        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
            continue

        merged[key] = value

    return merged


def _tidy_numbers(value):
    """
    Write 90 rather than 90.0.

    The API's form fields are floats, so a duration typed as 90 arrives as
    90.0 and the file fills up with trailing zeros over time. Whole numbers go
    back to int; 0.5 stays 0.5, and "60" stays the string it already was.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, dict):
        return {key: _tidy_numbers(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_tidy_numbers(item) for item in value]

    return value


def _dedupe_aliases(document):
    """
    Drop a column name listed twice, keeping the first spelling.

    ["HOOKLOAD", "HOOKLOAD_AVG", "Hookload"] is two names, not three: resolving
    against the table ignores case, so "Hookload" could never match anything
    "HOOKLOAD" had not already matched.
    """
    tidy = {}

    for logical, aliases in document.items():

        if not isinstance(aliases, list):
            tidy[logical] = aliases
            continue

        seen = set()
        kept = []

        for alias in aliases:
            key = alias.strip().lower() if isinstance(alias, str) else alias

            if key in seen:
                continue

            seen.add(key)
            kept.append(alias)

        tidy[logical] = kept

    return tidy


def _already_broken(name, complaint):
    """
    Was the file on disk failing this same check before the change?

    Without this, one old quirk anywhere in a file makes every later edit fail
    with a message about a part the writer never touched.
    """
    try:
        validate(name, load(name))

    except RuleFileError as existing:
        return str(existing) == complaint

    except Exception:
        return False

    return False


def save(name, document):
    """
    Check `document`, back up what is there, then replace it atomically.

    The agent may read the file at any moment, so it is written beside the real
    one and moved into place - a reader sees the old file or the new one, never
    a half-written one.
    """
    path = path_for(name)

    document = _tidy_numbers(document)

    if name == "column_mapping" and isinstance(document, dict):
        document = _dedupe_aliases(document)

    try:
        validate(name, document)

    except RuleFileError as exc:
        if _already_broken(name, str(exc)):
            raise RuleFileError(
                f"{exc}  (this was already the case in the file before your change - "
                "it is not something you just did, but it has to be fixed before "
                "anything can be written)"
            ) from exc

        raise

    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))

    temporary = path.with_suffix(".json.writing")

    with open(temporary, "w", encoding="utf-8") as fh:
        # ensure_ascii off, or the degree sign in "°C" is written back as
        # ° and the file stops being pleasant to read.
        json.dump(document, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())

    os.replace(temporary, path)

    log.info("%s updated (%d top-level entries)", path.name, len(document))

    return document


# The .bak file save() leaves behind is still written - it is the only copy of
# what a change replaced. There is no endpoint that reads it back: to undo a
# change, rename data/<file>.json.bak over data/<file>.json.


# ---------------------------------------------------------------------------
# A single well's rule set
#
# The files in data/ are the template every well starts from; each well then
# keeps its own copy, entered in the dashboard. Those four blocks are checked
# against each other rather than against the files on disk - a well may map
# its columns quite differently from the next one, and its ranges and activity
# flags have to agree with its own mapping, not with the template's.
# ---------------------------------------------------------------------------

# The order the data folder lists them in, which is the order the form asks
# for them in.
RULE_BLOCKS = ("activity", "column_mapping", "conditions", "ranges")


def _check_mapping_shape(document):
    """column_mapping on its own - structure, with no other file consulted."""
    _check_object(document, "column_mapping")

    for logical, aliases in document.items():
        if not isinstance(aliases, list) or not aliases:
            raise RuleFileError(
                f"column_mapping: '{logical}' must be a non-empty list of "
                "column names, e.g. [\"TOT_DPT_MD\", \"DEPTH\"]"
            )

        if not all(isinstance(a, str) and a.strip() for a in aliases):
            raise RuleFileError(
                f"column_mapping: every name for '{logical}' must be text"
            )


def validate_set(rules):
    """Raise RuleFileError unless `rules` is a usable set for one well."""
    if not isinstance(rules, dict):
        raise RuleFileError("The rules must be a JSON object")

    # Absent, null and empty all mean the same thing to whoever filled the
    # form in, so they get the same sentence back.
    missing = [block for block in RULE_BLOCKS if not rules.get(block)]

    if missing:
        raise RuleFileError(
            f"The rules are missing {', '.join(missing)}. All four blocks are "
            "required: " + ", ".join(RULE_BLOCKS)
        )

    # The mapping first: it decides which parameter names the other three are
    # allowed to mention.
    try:
        _check_mapping_shape(rules["column_mapping"])

        known = set(rules["column_mapping"])

        _check_activity(rules["activity"], known)
        _check_conditions(rules["conditions"], known)
        _check_ranges(rules["ranges"], known)

    except RuleFileError as exc:
        # The checks are shared with the file endpoints and name their subject
        # as "ranges.json". A well's rules were typed into a form and there is
        # no such file to go and open, so the same sentence is reworded to
        # name the block instead. Presentation only - nothing is re-checked.
        raise RuleFileError(str(exc).replace(".json", "")) from exc


def tidy_set(rules):
    """
    The same rules, written the way they are stored: 90 not 90.0, no alias
    listed twice.

    This runs before validate_set, so it has to survive rules that are not
    usable yet - a block left out entirely is passed through untouched for
    validate_set to complain about properly.
    """
    if not isinstance(rules, dict):
        raise RuleFileError("The rules must be a JSON object")

    tidy = {block: _tidy_numbers(rules.get(block)) for block in RULE_BLOCKS}

    if isinstance(tidy.get("column_mapping"), dict):
        tidy["column_mapping"] = _dedupe_aliases(tidy["column_mapping"])

    return tidy


def template():
    """The four files in data/, as the starting point for a new well."""
    return {block: load(block) for block in RULE_BLOCKS}
