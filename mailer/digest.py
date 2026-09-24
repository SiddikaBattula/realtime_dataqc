"""
What the ten-minute email says.

Given a window of time, this gathers every alert raised inside it, sorts the
wells into their base regions and writes one message per region. It touches no
network and keeps no clock of its own - the window is handed to it - so the
whole thing can be built and read without sending anything.

The one piece of judgement in here is collapsing repeats. A problem that
stands for the whole window is written to the alert file once a second, so ten
minutes of one stuck hookload is around six hundred identical lines. Mailing
those would bury the three things that actually happened. Alerts are therefore
grouped the way the dashboard groups them on a card: by the sentence with its
numbers blanked out, which is what makes two readings a second apart the same
problem rather than two problems.

    Hookload has remained unchanged for 30 seconds
        14:20:03 to 14:29:58   raised 612x

The count is the point. "612x" says a problem stood for the whole window; "1x"
says something happened once and cleared.
"""

import json
import re

from collections import OrderedDict

from config import Config
from logger import get_logger
from validation_realtime import alert_raised_at

log = get_logger(__name__)

# The "[24-09-26 21-30-58] " an alert starts with, and the numbers inside the
# sentence after it. Blanking both is what groups a standing problem into one
# line - the same rule alert_log._fingerprint uses to decide when to reprint.
_STAMP = re.compile(r"^\[[^\]]*\]\s*")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _fingerprint(alert):
    return _NUMBER.sub("#", _STAMP.sub("", alert))


def _message(alert):
    """The agent's sentence, without the timestamp it is stamped with."""
    return _STAMP.sub("", alert).strip()


def alerts_path(well):
    """Where a well's agent writes, and GET /alerts reads."""
    return Config.OUTPUT_DIR / well / "alerts.json"


def read_alerts(well):
    path = alerts_path(well)

    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            alerts = json.load(fh)

    except (OSError, json.JSONDecodeError) as exc:
        # The well's own agent rewrites this file constantly; a read that
        # catches it mid-write is a reason to skip this well for one digest,
        # not to lose the rest of the region's.
        log.warning("Could not read alerts for %s: %s", well, exc)
        return []

    return alerts if isinstance(alerts, list) else []


def in_window(alerts, start, end):
    """
    The alerts raised after `start` and no later than `end`.

    Half-open at the start so two consecutive windows cannot both claim an
    alert landing exactly on the boundary between them - 14:20:00 belongs to
    the 14:20-14:30 digest and not to the one before it.

    An alert with no readable timestamp is left out: it cannot be placed in a
    window, and guessing would either double-send it or send it in the wrong
    one.
    """
    kept = []

    for alert in alerts:
        raised = alert_raised_at(alert)

        if raised is not None and start < raised <= end:
            kept.append((raised, alert))

    return kept


def group(stamped):
    """
    One entry per distinct problem, in the order each was first seen.

    `stamped` is the (raised, alert) pairs from in_window. What comes back
    carries the sentence as it was last written - so the reading quoted is the
    most recent one, not the one from ten minutes ago - along with how many
    times it was raised and the span it covered.
    """
    groups = OrderedDict()

    for raised, alert in sorted(stamped, key=lambda pair: pair[0]):
        key = _fingerprint(alert)
        entry = groups.get(key)

        if entry is None:
            groups[key] = {
                "message": _message(alert),
                "count": 1,
                "first": raised,
                "last": raised,
            }
            continue

        entry["count"] += 1
        entry["last"] = raised
        entry["message"] = _message(alert)

    return list(groups.values())


def for_wells(wells, start, end):
    """
    [{well, groups, total}] for the wells that had anything to say.

    A well with nothing in the window is left out rather than listed as clear:
    the email is about what happened, and a list of quiet wells is the part
    nobody reads.
    """
    reported = []

    for well in sorted(wells):
        groups = group(in_window(read_alerts(well), start, end))

        if not groups:
            continue

        reported.append({
            "well": well,
            "groups": groups,
            "total": sum(entry["count"] for entry in groups),
        })

    return reported


def build(wells_by_region, regions, start, end):
    """
    One digest per region that has both somewhere to send and something to say.

    `wells_by_region` is region (casefolded) -> [well names]; `regions` is
    the same keys -> the row out of config.email_regions(). A region with wells
    but no row comes back in the second list rather than being silently skipped,
    because that is a base whose alerts nobody is being told about.
    """
    digests = []
    unaddressed = []

    for key, wells in sorted(wells_by_region.items()):
        row = regions.get(key)

        if row is None:
            unaddressed.append(sorted(wells))
            continue

        reported = for_wells(wells, start, end)

        if not reported:
            continue

        digests.append({
            "region": row["region"],
            "row": row,
            "wells": reported,
            "total": sum(item["total"] for item in reported),
            "start": start,
            "end": end,
        })

    return digests, unaddressed


# ---------------------------------------------------------------------------
# Wording
#
# Sent as text and HTML together. The text part is what a phone on a rig with
# one bar shows, and it is written to be readable on its own; the HTML is the
# same thing with the counts lined up.
# ---------------------------------------------------------------------------

def _clock(moment):
    return moment.strftime("%H:%M:%S")


def subject(digest):
    total = digest["total"]
    wells = len(digest["wells"])

    return (
        "[DataQC] {region}: {total} alert{s} on {wells} well{ws} "
        "({start}-{end})".format(
            region=digest["region"],
            total=total,
            s="" if total == 1 else "s",
            wells=wells,
            ws="" if wells == 1 else "s",
            start=_clock(digest["start"]),
            end=_clock(digest["end"]),
        )
    )


def text_body(digest):
    lines = [
        "Base region : {}".format(digest["region"]),
        "Window      : {:%d-%m-%Y %H:%M:%S} to {:%H:%M:%S}".format(
            digest["start"], digest["end"]
        ),
        "Alerts      : {} on {} well(s)".format(
            digest["total"], len(digest["wells"])
        ),
        "",
    ]

    for item in digest["wells"]:
        lines.append(item["well"])
        lines.append("-" * len(item["well"]))

        for entry in item["groups"]:
            times = _clock(entry["first"])

            if entry["count"] > 1:
                times += " to " + _clock(entry["last"])

            lines.append("  " + entry["message"])
            lines.append("      {}   raised {}x".format(times, entry["count"]))

        lines.append("")

    lines.append(
        "Raised by Real-Time Data QC. Alerts are not written to any rig "
        "database."
    )

    return "\n".join(lines)


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Inline styles throughout: mail clients strip <style> blocks, so anything not
# written on the element itself is not there by the time it is read.
_CELL = "padding:5px 10px 5px 0;border-bottom:1px solid #e6e9f0;"


def html_body(digest):
    parts = [
        '<div style="font:14px -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'color:#1b1f2a">',
        '<h2 style="margin:0 0 4px;font-size:17px">{}</h2>'.format(
            _escape(digest["region"])
        ),
        '<p style="margin:0 0 16px;color:#5b6377;font-size:13px">'
        '{:%d-%m-%Y %H:%M:%S} to {:%H:%M:%S} &middot; {} alert(s) on {} well(s)'
        '</p>'.format(
            digest["start"], digest["end"], digest["total"], len(digest["wells"])
        ),
    ]

    for item in digest["wells"]:
        parts.append(
            '<h3 style="margin:18px 0 6px;font-size:14px">{}'
            ' <span style="color:#8a91ac;font-weight:400">{} alert(s)</span>'
            '</h3>'.format(_escape(item["well"]), item["total"])
        )
        parts.append(
            '<table cellpadding="0" cellspacing="0" style="width:100%;'
            'border-collapse:collapse;font-size:13px">'
        )

        for entry in item["groups"]:
            times = _clock(entry["first"])

            if entry["count"] > 1:
                times += " &ndash; " + _clock(entry["last"])

            parts.append(
                '<tr>'
                '<td style="{cell}white-space:nowrap;color:#5b6377">{times}</td>'
                '<td style="{cell}">{message}</td>'
                '<td style="{cell}text-align:right;white-space:nowrap;'
                'color:#5b6377">&times;{count}</td>'
                '</tr>'.format(
                    cell=_CELL,
                    times=times,
                    message=_escape(entry["message"]),
                    count=entry["count"],
                )
            )

        parts.append('</table>')

    parts.append(
        '<p style="margin:22px 0 0;color:#8a91ac;font-size:12px">'
        'Raised by Real-Time Data QC. Alerts are not written to any rig '
        'database.</p></div>'
    )

    return "".join(parts)
