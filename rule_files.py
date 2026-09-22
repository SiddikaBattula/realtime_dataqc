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
import math
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
    # Unlike the four above, not a template: what each parameter is called in
    # alert text, shared by every well and read by every agent as it changes.
    "display_name": Config.DISPLAY_NAME_FILE,
}

# Files that may be missing - no display names just means every parameter is
# called by its own name.
OPTIONAL_FILES = {"display_name"}

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
        if name in OPTIONAL_FILES:
            return {}

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


def _check_display_name(document, known):
    # Empty is allowed: no display names at all.
    if not isinstance(document, dict):
        raise RuleFileError("display_name.json must be a JSON object")

    # Not checked against column_mapping: that is only the template, and a
    # well may map a parameter the template does not have.
    for param, name in document.items():
        if not param.strip():
            raise RuleFileError("display_name.json: a parameter name cannot be blank")

        if not isinstance(name, str) or not name.strip():
            raise RuleFileError(
                f"display_name.json: '{param}' must be shown as some text, got {name!r}"
            )


CHECKS = {
    "activity": _check_activity,
    "conditions": _check_conditions,
    "ranges": _check_ranges,
    "column_mapping": _check_column_mapping,
    "display_name": _check_display_name,
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


def _tidy_display_names(document):
    """
    Spaces trimmed, and a name left blank dropped.

    A blank box in the form means "call it by its own name", which is what
    having no entry already does - storing "" would print an empty name.
    Anything that is not text is left for the check to refuse.
    """
    tidy = {}

    for param, name in document.items():
        param = param.strip()

        if isinstance(name, str):
            name = name.strip()

            if not name:
                continue

        tidy[param] = name

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

    if name == "display_name" and isinstance(document, dict):
        document = _tidy_display_names(document)

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
# for them in. The first four are objects; drilling_criteria is a single
# number, and is here because it is saved, checked and sent with the rest.
RULE_BLOCKS = (
    "activity",
    "column_mapping",
    "conditions",
    "ranges",
    "drilling_criteria",
    "bit_depth_threshold"
)

# Metres of hole depth minus bit depth that still count as on bottom. It used
# to be one DRILLING_CRITERIA in .env for every rig; each well now carries its
# own, because what counts as off bottom is a property of the rig's depth
# sensors and not of the machine the agent runs on. This is only what a new
# well's form opens with, and what a well saved before the change is read
# under - see well_rules._read. Set it in .env as DEFAULT_DRILLING_CRITERIA.
DEFAULT_DRILLING_CRITERIA = Config.DEFAULT_DRILLING_CRITERIA
DEFAULT_BIT_DEPTH_THRESHOLD = Config.DEFAULT_BIT_DEPTH_THRESHOLD

def drilling_criteria_of(rules):
    """
    One well's drilling criteria, as a number the validator can compare with.

    Anything unusable - missing, null, left over from before the setting
    existed - falls back to the default rather than raising: a well is better
    checked against 0.1 m than not checked at all. save() is where a bad value
    is refused, and it is refused there before it can ever be stored.
    """
    if not isinstance(rules, dict):
        return DEFAULT_DRILLING_CRITERIA

    try:
        criteria = _as_number(
            rules.get("drilling_criteria"), "drilling_criteria"
        )

    except RuleFileError:
        return DEFAULT_DRILLING_CRITERIA

    if not math.isfinite(criteria) or criteria < 0:
        return DEFAULT_DRILLING_CRITERIA

    return criteria


# The two activities the validator tells apart: bit on bottom, and anything
# else. The second used to be called RIH, and rules saved before the rename
# still have that block under the old name.
DRILLING = "DRILLING"
NON_DRILLING = "NON DRILLING"

_OLD_ACTIVITY_NAMES = {"RIH": NON_DRILLING}


def rename_old_activities(activity):
    """
    `activity` with a block still under an old name moved to its new one.

    Without this a well saved before the rename keeps its off-bottom rules
    under RIH, which the validator never asks for: every NON DRILLING reading
    would raise "Unknown activity" and none of those checks would run. If a
    block already exists under the new name, that one is kept.
    """
    if not isinstance(activity, dict):
        return activity

    renamed = {}

    for name, rules in activity.items():
        new_name = _OLD_ACTIVITY_NAMES.get(name, name)

        if new_name != name and new_name in activity:
            continue

        renamed[new_name] = rules

    return renamed


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
    missing = [
        block
        for block in RULE_BLOCKS
        if rules.get(block) is None
    ]

    if missing:
        raise RuleFileError(
            f"The rules are missing {', '.join(missing)}. All of these are "
            "required: " + ", ".join(RULE_BLOCKS)
        )

    # Checked with the same _as_number as every other figure in the rules, so
    # "0.1" typed into the form is read the way "60" in a factor already is,
    # and true/false is refused rather than counted as 1.
    criteria = _as_number(
        rules["drilling_criteria"],
        "drilling_criteria (the off-bottom margin, in metres)",
    )

    if not math.isfinite(criteria):
        raise RuleFileError(
            "drilling_criteria (the off-bottom margin, in metres) must be an "
            "ordinary number - Infinity and NaN cannot be compared against"
        )

    if criteria < 0:
        raise RuleFileError(
            "drilling_criteria (the off-bottom margin, in metres) cannot be "
            "negative - it is how far the bit may sit above bottom and still "
            "count as DRILLING, so the smallest it goes is 0"
        )

    bit_depth_threshold = _as_number(
        rules["bit_depth_threshold"],
        "bit_depth_threshold"
    )

    if not math.isfinite(bit_depth_threshold):
        raise RuleFileError(
            "bit_depth_threshold must be a valid number"
        )

    if bit_depth_threshold < 0:
        raise RuleFileError(
            "bit_depth_threshold cannot be negative"
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

    tidy["activity"] = rename_old_activities(tidy["activity"])

    # "0.1" out of a text box is the number 0.1, and is stored as one - the
    # validator compares it against a depth and never re-reads the file. A
    # value that is not a number at all is left exactly as it came in, for
    # validate_set to name properly.
    if isinstance(tidy["drilling_criteria"], str):
        try:
            tidy["drilling_criteria"] = _tidy_numbers(
                _as_number(tidy["drilling_criteria"], "drilling_criteria")
            )
        except RuleFileError:
            pass

    if isinstance(tidy.get("column_mapping"), dict):
        tidy["column_mapping"] = _dedupe_aliases(tidy["column_mapping"])

    return tidy


def template():
    rules = {
        "activity": load("activity"),
        "column_mapping": load("column_mapping"),
        "conditions": load("conditions"),
        "ranges": load("ranges"),
        "drilling_criteria": DEFAULT_DRILLING_CRITERIA,
        "bit_depth_threshold": DEFAULT_BIT_DEPTH_THRESHOLD,
    }

    rules["activity"] = rename_old_activities(
        rules["activity"]
    )

    return rules
