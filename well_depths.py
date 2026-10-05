"""
The bit depth and total depth shown on each well's card.

Published from the raw row on every successful read, before any check runs,
so the card follows the rig live whether or not anything is wrong with the
reading - and a check that fails cannot blank it. Only losing the rig clears
it, because a depth from before the link dropped would look like a live one.
"""

import well_registry
from column_mapper import to_number


def publish(database_name, validator, row):
    """Put this row's BIT_DPT_MD and DEPTH on the well's card."""
    mapper = validator.mapper

    def read(logical):
        column = mapper.column_for(logical)
        return to_number(row.get(column)) if column else None

    well_registry.set_depths(
        database_name,
        read("BIT_DPT_MD"),
        read("DEPTH"),
        validator.ranges.get("DEPTH", {}).get("unit", ""),
    )


def clear(database_name):
    """The rig cannot be reached: show "—" rather than the last depth."""
    well_registry.set_depths(database_name, None, None)