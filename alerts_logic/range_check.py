"""
Check 2 - ranges.

Each parameter inside its min/max from its ranges block, after `factor`
converts the reading into the limits' unit - multiplying, or dividing for
ROP (see INVERSE_PARAMS in constants.py).
"""

from column_mapper import to_number

from .constants import INVERSE_PARAMS


def run_range_checks(validator, normalized_data, raise_alert, date_str,
                      bit_depth, depth_unit, total_spm):
    for param, limits in validator.ranges.items():
        if param == "SPM":
            value = total_spm

            if value <= 0:
                continue
        else:
            value = normalized_data.get(param)

            if value is None:
                continue

        # min, max and factor are tolerated as strings in the file
        # ("60"), so they are coerced here rather than compared raw -
        # comparing a float against a str is a TypeError, and it would
        # take down the whole reading, not just this one check.
        min_val = to_number(limits.get("min"))
        max_val = to_number(limits.get("max"))

        if min_val is None or max_val is None:
            validator.log.error(
                "ranges.json: '%s' has a min/max that is not a number "
                "(%r / %r) - range check skipped",
                param, limits.get("min"), limits.get("max"),
            )
            continue

        # Compared as floats, quoted as they are written in the file, so a
        # limit of 200 still reads "200" in the alert and not "200.0".
        min_text = limits["min"]
        max_text = limits["max"]

        unit = limits.get("unit", "")

        # `factor` converts the stored reading into the unit the limits
        # are written in, before either is compared. ROP is the reason it
        # exists: the table stores it in minutes per metre and the limits
        # are in m/hr. Without this the limits were being applied to the
        # raw column, which is the unit they were never written for.
        raw = value
        value = validator._in_limit_unit(param, value)

        if value is None:
            # ROP at 0: the bit is not advancing, which is normal on every
            # connection and trip. There is no speed to range-check, so
            # this reading says nothing about the parameter either way.
            validator.log.debug("%s is 0 - nothing to convert, range check skipped", param)
            continue

        if value < min_val:
            raise_alert(
                f"[{date_str}] {validator.display_name(param)} : {value:.2f}{unit} below limit {min_text}{unit} BD : {bit_depth}{depth_unit} ",
                param,
                subject=f"RANGE:{param}",
                value=f"below {value:.2f}",
                why=validator.alert_log.range_reason(
                    param, raw, value, limits, "below", min_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
            )

        elif value > max_val:
            raise_alert(
                f"[{date_str}] {validator.display_name(param)} : {value:.2f}{unit} above limit {max_text}{unit}  BD : {bit_depth}{depth_unit}",
                param,
                subject=f"RANGE:{param}",
                value=f"above {value:.2f}",
                why=validator.alert_log.range_reason(
                    param, raw, value, limits, "above", max_text,
                    inverse=param.upper() in INVERSE_PARAMS,
                ),
            )