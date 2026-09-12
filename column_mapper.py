"""
Single place where physical table columns become logical parameter names.

Everything downstream (ranges.json, activity.json, conditions.json, the
validator) speaks ONLY logical names: SPP, ROP, HOOKLOAD, ...
When the source table changes, edit data/column_mapping.json and nothing else.
"""

import json
from decimal import Decimal
from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

# Logical columns that must stay as-is (not coerced to float).
NON_NUMERIC = {"TIME", "ACTIVITY"}


class ColumnMappingError(Exception):
    """Raised when the mapping file is malformed or a critical column is absent."""


def to_number(value):
    """Best-effort numeric coercion. Returns None if not usable as a number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "nan", "-", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class ColumnMapper:
    def __init__(self, mapping, critical=None):
        self.mapping = mapping
        self.critical = set(critical or [])
        self.resolved = {}       # logical -> actual column name in the table
        self.missing = []        # logical names with no matching column
        self.unmapped = []       # table columns not referenced by the mapping
        self._resolved_once = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def from_mapping(cls, mapping, critical=None, source="the well's rules"):
        """
        A mapper from a mapping already in memory.

        This is the one a well agent uses: its mapping comes out of that well's
        own record, not off disk, because two rigs on the same server can name
        their columns quite differently.
        """
        if not isinstance(mapping, dict) or not mapping:
            raise ColumnMappingError(f"{source}: column mapping must be a non-empty object")

        for logical, aliases in mapping.items():
            if not isinstance(aliases, list) or not aliases:
                raise ColumnMappingError(
                    f"{source}: '{logical}' must map to a non-empty list of column names"
                )
            if not all(isinstance(a, str) and a.strip() for a in aliases):
                raise ColumnMappingError(
                    f"{source}: aliases for '{logical}' must all be non-empty strings"
                )

        log.info("Column mapping from %s (%d logical columns)", source, len(mapping))
        return cls(mapping, critical)

    @classmethod
    def from_file(cls, path, critical=None):
        path = Path(path)
        if not path.exists():
            raise ColumnMappingError(f"Column mapping file not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as fh:
                mapping = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ColumnMappingError(f"{path} is not valid JSON: {exc}") from exc

        return cls.from_mapping(mapping, critical, source=str(path))

    # ------------------------------------------------------------------
    # Resolution against the live table
    # ------------------------------------------------------------------
    def resolve(self, table_columns):
        """
        Match every logical name to a real column of the table (case-insensitive).
        Logs a warning per unmatched column; raises if a CRITICAL one is unmatched.
        """
        lookup = {}
        for col in table_columns:
            lookup.setdefault(str(col).strip().lower(), str(col))

        self.resolved.clear()
        self.missing.clear()

        for logical, aliases in self.mapping.items():
            match = None
            for alias in aliases:
                key = alias.strip().lower()
                if key in lookup:
                    match = lookup[key]
                    break

            if match:
                self.resolved[logical] = match
                log.debug("Mapped %-16s -> %s", logical, match)
            else:
                self.missing.append(logical)
                message = (
                    "Column mapping missing for '%s'. None of %s exist in the table. "
                    "Add the correct column name to column_mapping.json."
                )
                if logical in self.critical:
                    log.error(message, logical, aliases)
                else:
                    log.warning(message + " Checks using it will be skipped.",
                                logical, aliases)

        used = {c.lower() for c in self.resolved.values()}
        self.unmapped = sorted(c for c in lookup.values() if c.lower() not in used)

        blocking = [m for m in self.missing if m in self.critical]
        if blocking:
            raise ColumnMappingError(
                "Cannot start: no table column found for required logical column(s): "
                + ", ".join(blocking)
                + ". Fix data/column_mapping.json."
            )

        log.info(
            "Column mapping resolved: %d/%d matched, %d missing, %d table columns unused",
            len(self.resolved), len(self.mapping), len(self.missing), len(self.unmapped),
        )
        if self.unmapped:
            log.debug("Table columns not referenced by the mapping: %s",
                      ", ".join(self.unmapped))

        self._resolved_once = True
        return dict(self.resolved)

    # ------------------------------------------------------------------
    # Per-row normalisation
    # ------------------------------------------------------------------
    def normalize(self, row):
        """
        Turn a raw DB row into {logical_name: value}.

        Returns (normalized, errors). `errors` holds per-row problems only
        (column resolved at startup but absent/NULL in this row).
        Structural problems are reported once, at resolve() time.
        """
        if not self._resolved_once:
            raise ColumnMappingError("resolve() must be called before normalize()")

        normalized = {logical: None for logical in self.mapping}
        errors = []

        for logical, column in self.resolved.items():
            if column not in row:
                errors.append(
                    f"Column '{column}' (logical '{logical}') disappeared from the result set"
                )
                continue

            raw = row[column]
            if logical in NON_NUMERIC:
                normalized[logical] = raw
                continue

            value = to_number(raw)
            if value is None and raw is not None:
                errors.append(
                    f"Value for '{logical}' (column '{column}') is not numeric: {raw!r}"
                )
            normalized[logical] = value

        return normalized, errors

    def column_for(self, logical):
        return self.resolved.get(logical)

    def is_available(self, logical):
        return logical in self.resolved