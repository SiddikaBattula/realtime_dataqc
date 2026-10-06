# """
# What the ten-minute email says.

# Given a window of time, this gathers every alert raised inside it, sorts the
# wells into their base regions and writes one message per region. It touches no
# network and keeps no clock of its own - the window is handed to it - so the
# whole thing can be built and read without sending anything.

# The one piece of judgement in here is collapsing repeats. A problem that
# stands for the whole window is written to the alert file once a second, so ten
# minutes of one stuck hookload is around six hundred identical lines. Mailing
# those would bury the three things that actually happened. Alerts are therefore
# grouped the way the dashboard groups them on a card: by the sentence with its
# numbers blanked out, which is what makes two readings a second apart the same
# problem rather than two problems.

#     Hookload has remained unchanged for 30 seconds
#         14:20:03 to 14:29:58   raised 612x

# The count is the point. "612x" says a problem stood for the whole window; "1x"
# says something happened once and cleared.
# """

# import well_agent
# import json
# import re

# from collections import OrderedDict

# from config import Config
# from logger import get_logger
# from validation_realtime import alert_raised_at

# log = get_logger(__name__)

# # The "[24-09-26 21-30-58] " an alert starts with, and the numbers inside the
# # sentence after it. Blanking both is what groups a standing problem into one
# # line - the same rule alert_log._fingerprint uses to decide when to reprint.
# _STAMP = re.compile(r"^\[[^\]]*\]\s*")
# _NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

# import re

# CRITICAL_PATTERNS = [
#     re.compile(r"Please check for data Trans", re.I),
#     re.compile(r"increased by", re.I),
#     re.compile(r"Bit Depth jump by", re.I),
# ]

# def is_critical(message):
#     return any(pattern.search(message) for pattern in CRITICAL_PATTERNS)


# def _fingerprint(alert):
#     return _NUMBER.sub("#", _STAMP.sub("", alert))


# def _message(alert):
#     """The agent's sentence, without the timestamp it is stamped with."""
#     return _STAMP.sub("", alert).strip()


# def alerts_path(well):
#     """Where a well's agent writes, and GET /alerts reads."""
#     return Config.OUTPUT_DIR / well / "alerts.json"


# def read_alerts(well):
#     path = alerts_path(well)

#     if not path.exists():
#         return []

#     try:
#         with open(path, "r", encoding="utf-8") as fh:
#             alerts = json.load(fh)

#     except (OSError, json.JSONDecodeError) as exc:
#         # The well's own agent rewrites this file constantly; a read that
#         # catches it mid-write is a reason to skip this well for one digest,
#         # not to lose the rest of the region's.
#         log.warning("Could not read alerts for %s: %s", well, exc)
#         return []

#     return alerts if isinstance(alerts, list) else []


# def in_window(alerts, start, end):
#     """
#     The alerts raised after `start` and no later than `end`.

#     Half-open at the start so two consecutive windows cannot both claim an
#     alert landing exactly on the boundary between them - 14:20:00 belongs to
#     the 14:20-14:30 digest and not to the one before it.

#     An alert with no readable timestamp is left out: it cannot be placed in a
#     window, and guessing would either double-send it or send it in the wrong
#     one.
#     """
#     kept = []

#     for alert in alerts:
#         raised = alert_raised_at(alert)

#         if raised is not None and start < raised <= end:
#             kept.append((raised, alert))

#     return kept


# def group(stamped):
#     """
#     One entry per distinct problem, in the order each was first seen.

#     `stamped` is the (raised, alert) pairs from in_window. What comes back
#     carries the sentence as it was last written - so the reading quoted is the
#     most recent one, not the one from ten minutes ago - along with how many
#     times it was raised and the span it covered.
#     """
#     groups = OrderedDict()

#     for raised, alert in sorted(stamped, key=lambda pair: pair[0]):
#         key = _fingerprint(alert)
#         entry = groups.get(key)

#         if entry is None:
#             groups[key] = {
#                 "message": _message(alert),
#                 "count": 1,
#                 "first": raised,
#                 "last": raised,
#             }
#             continue

#         entry["count"] += 1
#         entry["last"] = raised
#         entry["message"] = _message(alert)

#     return list(groups.values())

# def for_wells(wells, start, end):
#     """
#     [{well, groups, total}] for the wells that had anything to say.

#     Only critical alerts are considered - everything else is read from disk
#     (and still shown on the dashboard) but never makes it into a digest.
#     """
#     reported = []

#     for well in sorted(wells):
#         alerts = [a for a in read_alerts(well) if is_critical(_message(a))]
#         window_alerts = in_window(alerts, start, end)

#         if not window_alerts:
#             continue

#         reported.append({
#             "well": well,
#             "alerts": window_alerts,
#             "total": len(window_alerts),
#         })

#     return reported


# def build(wells_by_region, regions, start, end):
#     """
#     One digest per region that has both somewhere to send and something to say.

#     `wells_by_region` is region (casefolded) -> [well names]; `regions` is
#     the same keys -> the row out of config.email_regions(). A region with wells
#     but no row comes back in the second list rather than being silently skipped,
#     because that is a base whose alerts nobody is being told about.
#     """
#     digests = []
#     unaddressed = []

#     for key, wells in sorted(wells_by_region.items()):
#         row = regions.get(key)

#         if row is None:
#             unaddressed.append(sorted(wells))
#             continue

#         reported = for_wells(wells, start, end)

#         if not reported:
#             continue

#         digests.append({
#             "region": row["region"],
#             "row": row,
#             "wells": reported,
#             "total": sum(item["total"] for item in reported),
#             "start": start,
#             "end": end,
#         })

#     return digests, unaddressed


# # ---------------------------------------------------------------------------
# # Wording
# #
# # Sent as text and HTML together. The text part is what a phone on a rig with
# # one bar shows, and it is written to be readable on its own; the HTML is the
# # same thing with the counts lined up.
# # ---------------------------------------------------------------------------

# def _clock(moment):
#     return moment.strftime("%H:%M:%S")


# def subject(digest):
#     total = digest["total"]
#     wells = len(digest["wells"])

#     return "Automated Data Quality Alert Summary"


# def text_body(digest):
#     lines = [
#         "Dear Team",
#         "",
#         "Base region: {}".format(digest["region"]),
#         "",
#     ]

#     for item in digest["wells"]:
#         lines.append("well name: {}".format(item["well"]))
#         lines.append("alerts:")

#         for raised, alert in item["alerts"]:

#             lines.append(
#                 f"• {_clock(raised)} | {_message(alert)}"
#             )

#         lines.append("")

#     lines.append("Please check it")
#     lines.append("")
#     lines.append("Regards")
#     lines.append("Real-Time Data QC")

#     return "\n".join(lines)


# def _escape(text):
#     return (
#         str(text)
#         .replace("&", "&amp;")
#         .replace("<", "&lt;")
#         .replace(">", "&gt;")
#     )


# # Inline styles throughout: mail clients strip <style> blocks, so anything not
# # written on the element itself is not there by the time it is read.
# _CELL = "padding:5px 10px 5px 0;border-bottom:1px solid #e6e9f0;"

# def html_body(digest):
#     parts = [
#         '<div style="font-family:Segoe UI,Arial,sans-serif;'
#         'font-size:14px;line-height:1.6;color:#333;">',

#         '<p>Dear Team,</p>',
#         '<p>An automated Data Quality Control (QC) check has flagged the following exception </p>'
#     ]
   
#     for item in digest["wells"]:

#         parts.append(
#             f'<p>Well Name: {_escape(item["well"])}</p>'
#         )

#         parts.append('<ul>')

#         for raised, alert in item["alerts"]:

#             parts.append(
#                 f'<li>{_clock(raised)} | {_escape(_message(alert))}</li>'
#             )

#         parts.append('</ul>')

#     parts.extend([
#         '<br><p>Please review the identified condition and take appropriate corrective action if required.</p>',
#         '<p>Kindly investigate and provide feedback if necessary.</p>',
#         '<p>Regards,<br><b>Automated Data QC Monitoring System</b></p>',
#         '</div>'
#     ])

#     return "".join(parts)








"""
What the alert-digest email says.

Given a window of time, this gathers the alerts raised inside it, sorts the
wells into their base regions and writes one message per region. It touches no
network and keeps no clock of its own - the window is handed to it - so the
whole thing can be built and read without sending anything.

Two kinds of alert are mailed, and each is written differently:

  1. EVENTS - "... increased by ..." (ROP) and "Bit Depth jump by ...".
     Every one is listed on its own line with the time it was raised:

         20:53:36 | ROP increased by 217.74%(30.50m/hr), BD:1722.05m

  2. HOOKLOAD NOT CHANGING ("Please check for data Trans...").
     Summarised, never listed raw. The raised / still-unchanged / resolved
     alerts are joined into one entry per occurrence, so the mail says when
     the alert came and when it cleared:

         alert raised 11:13:04, resolved 11:14:05 (Hookload was unchanged
         at 27.0 for 61 s)

     An occurrence that has not cleared says so, and one that was raised in
     an earlier digest and cleared in this one is reported as resolved.

Everything else is on the dashboard only.
"""

import json
import re

from datetime import timedelta

from config import Config
from logger import get_logger
from validation_realtime import alert_raised_at

log = get_logger(__name__)

# The "[24-09-26 21-30-58] " an alert starts with.
_STAMP = re.compile(r"^\[[^\]]*\]\s*")

_NUM = r"[-+]?\d+(?:\.\d+)?"

# Alerts mailed one line each.
EVENT_PATTERNS = [
    re.compile(r"increased by", re.I),
    re.compile(r"Bit Depth jump by", re.I),
    re.compile(r"cannot be 0")
]

# The three messages hookload_check.py writes.
_HL_RAISED = re.compile(r"Please check for data Trans", re.I)
_HL_STILL = re.compile(r"is STILL unchanged", re.I)
_HL_RESOLVED = re.compile(r"alert resolved - value is changing again", re.I)

_HL_RAISED_DATA = re.compile(r"unchanged at (%s) for (\d+) seconds" % _NUM, re.I)
_HL_STILL_DATA = re.compile(r"unchanged at (%s) - stuck for (\d+) minutes" % _NUM, re.I)
_HL_RESOLVED_DATA = re.compile(
    r"was stuck at (%s) for (\d+) seconds, now (%s)" % (_NUM, _NUM), re.I
)


def _hookload_kind(message):
    """'resolved' | 'still' | 'raised' for a hookload message, else None."""
    if _HL_RESOLVED.search(message):
        return "resolved"

    if _HL_STILL.search(message):
        return "still"

    if _HL_RAISED.search(message):
        return "raised"

    return None


def is_event(message):
    return any(pattern.search(message) for pattern in EVENT_PATTERNS)


def is_critical(message):
    """Whether this alert can appear in an email at all."""
    return is_event(message) or _hookload_kind(message) is not None


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
    The (raised, alert) pairs raised after `start` and no later than `end`.

    Half-open at the start so two consecutive windows cannot both claim an
    alert landing exactly on the boundary between them. An alert with no
    readable timestamp is left out: it cannot be placed in a window.
    """
    kept = []

    for alert in alerts:
        raised = alert_raised_at(alert)

        if raised is not None and start < raised <= end:
            kept.append((raised, alert))

    return kept


# ---------------------------------------------------------------------------
# Hookload: raised / still unchanged / resolved  ->  one entry per occurrence
# ---------------------------------------------------------------------------

def _new_episode():
    return {
        "raised": None,          # when the alert was raised (None = earlier digest)
        "resolved": None,        # when it cleared (None = still not resolved)
        "value": None,           # the value it was frozen at
        "frozen_for": None,      # seconds frozen, as reported on resolve
        "new_value": None,       # what it changed to when it resolved
        "still_minutes": None,   # minutes stuck, as reported by the 30-min reminder
        "last_reminder": None,
    }


def hookload_episodes(events):
    """
    Join hookload alerts into occurrences, oldest first.

    `events` are (raised, message) pairs. A "raised" alert opens one, a
    "still unchanged" reminder updates it, a "resolved" message closes it.
    A reminder or resolve with nothing open means the occurrence began in an
    earlier digest, so it is reported with raised=None.
    """
    episodes = []
    current = None

    for raised, message in sorted(events, key=lambda pair: pair[0]):
        kind = _hookload_kind(message)

        if kind == "raised":
            if current is not None:
                episodes.append(current)     # never saw it resolve

            current = _new_episode()
            current["raised"] = raised

            data = _HL_RAISED_DATA.search(message)
            if data:
                current["value"] = data.group(1)

        elif kind == "still":
            if current is None:
                current = _new_episode()

            current["last_reminder"] = raised

            data = _HL_STILL_DATA.search(message)
            if data:
                current["value"] = data.group(1)
                current["still_minutes"] = int(data.group(2))

        elif kind == "resolved":
            if current is None:
                current = _new_episode()

            current["resolved"] = raised

            data = _HL_RESOLVED_DATA.search(message)
            if data:
                current["value"] = data.group(1)
                current["frozen_for"] = int(data.group(2))
                current["new_value"] = data.group(3)

            episodes.append(current)
            current = None

    if current is not None:
        episodes.append(current)

    return episodes


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def for_wells(wells, start, end):
    """
    [{well, events, hookload, total}] for the wells with something to report.

    events    - [(raised, message)], one per ROP-increase / depth-jump alert
    hookload  - None, or {"episodes": [...], "resolved": n, "open": n}
    """
    reported = []

    for well in sorted(wells):
        alerts = [a for a in read_alerts(well) if is_critical(_message(a))]
        window_alerts = in_window(alerts, start, end)

        if not window_alerts:
            continue

        events = []
        hookload_alerts = []

        for raised, alert in window_alerts:
            message = _message(alert)

            if is_event(message):
                events.append((raised, message))
            else:
                hookload_alerts.append((raised, message))

        events.sort(key=lambda pair: pair[0])

        hookload = None
        episodes = hookload_episodes(hookload_alerts)

        if episodes:
            resolved = sum(1 for ep in episodes if ep["resolved"] is not None)
            hookload = {
                "episodes": episodes,
                "resolved": resolved,
                "open": len(episodes) - resolved,
            }

        reported.append({
            "well": well,
            "events": events,
            "hookload": hookload,
            "total": len(events) + len(episodes),
        })

    return reported


def build(wells_by_region, regions, start, end):
    """
    One digest per region that has both somewhere to send and something to say.

    A region with wells but no row comes back in the second list rather than
    being silently skipped, because that is a base whose alerts nobody is being
    told about.
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
# ---------------------------------------------------------------------------

def _clock(moment):
    return moment.strftime("%H:%M:%S")


def _duration(seconds):
    seconds = int(seconds)

    if seconds < 60:
        return "{} s".format(seconds)

    minutes, rest = divmod(seconds, 60)

    if minutes < 60:
        return "{} min {} s".format(minutes, rest) if rest else "{} min".format(minutes)

    hours, minutes = divmod(minutes, 60)

    return "{} h {} min".format(hours, minutes) if minutes else "{} h".format(hours)


def _tidy(message):
    """'217.74%(30.50m/hr), BD:1722.05m' -> '217.74% (30.50 m/hr), BD: 1722.05 m'."""
    message = message.replace("%(", "% (")
    message = re.sub(r"(\d)(m/hr|m)\b", r"\1 \2", message)
    message = re.sub(r"BD:(?=\S)", "BD: ", message)
    return message


def _event_line(raised, message):
    """'20:53:36 | ROP increased by 217.74% (30.50 m/hr), BD: 1722.05 m'."""
    return "{} | {}".format(_clock(raised), _tidy(message))


def _hookload_header(hookload):
    total = len(hookload["episodes"])

    parts = ["{} resolved".format(hookload["resolved"])]

    if hookload["open"]:
        parts.append("{} not resolved yet".format(hookload["open"]))

    return "Data Transmission Stopped - {} alert(s): {}".format(
        total, ", ".join(parts)
    )


def _episode_lines(ep):
    """One occurrence as short lines: raised / resolved / duration."""
    at = " at {}".format(ep["value"]) if ep["value"] is not None else ""

    if ep["raised"] is not None:
        raised = "alert raised at {}".format(_clock(ep["raised"]))
    else:
        raised = "alert raised in an earlier summary"

    lines = [raised]

    if ep["resolved"] is not None:
        lines.append(
            "resolved at {}".format(_clock(ep["resolved"]))
        )

        if ep["frozen_for"] is not None:
            lines.append(
                "duration: {}".format(_duration(ep["frozen_for"]))
            )

        return lines

    lines.append("NOT resolved yet")

    if ep["still_minutes"] is not None:
        lines.append("still unchanged after {} minutes (checked {})".format(
            ep["still_minutes"], _clock(ep["last_reminder"])
        ))

    return lines


def subject(digest):
    return "Automated Data Quality Alert Summary"


def text_body(digest):
    lines = [
        "Dear Team,",
        "",
        "An automated Data Quality Control (QC) check has flagged the "
        "following exception(s).",
        "",
        "Base region: {}".format(digest["region"]),
        "",
    ]

    for item in digest["wells"]:
        lines.append("Well name: {}".format(item["well"]))

        for raised, message in item["events"]:
            lines.append("• " + _event_line(raised, message))

        if item["hookload"]:
            lines.append("• " + _hookload_header(item["hookload"]))

            for ep in item["hookload"]["episodes"]:
                for number, line in enumerate(_episode_lines(ep)):
                    lines.append(("    - " if number == 0 else "      ") + line)

        lines.append("")

    lines.append(
        "Please review the identified condition and take appropriate "
        "corrective action if required."
    )
    lines.append("")
    lines.append("Regards,")
    lines.append("Automated Data QC Monitoring System")

    return "\n".join(lines)


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def html_body(digest):
    parts = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;'
        'font-size:14px;line-height:1.6;color:#333;">',
        '<p>Dear Team,</p>',
        '<p>An automated Data Quality Control (QC) check has flagged the '
        'following exception(s).</p>',
        '<p>Base region: {}</p>'.format(_escape(digest["region"])),
    ]

    for item in digest["wells"]:
        parts.append('<p><b>Well name: {}</b></p>'.format(_escape(item["well"])))
        parts.append('<ul>')

        for raised, message in item["events"]:
            parts.append('<li>{}</li>'.format(_escape(_event_line(raised, message))))

        if item["hookload"]:
            parts.append('<li>{}<ul>'.format(_escape(_hookload_header(item["hookload"]))))

            for ep in item["hookload"]["episodes"]:
                parts.append('<li>{}</li>'.format(
                    "<br>".join(_escape(line) for line in _episode_lines(ep))
                ))

            parts.append('</ul></li>')

        parts.append('</ul>')

    parts.extend([
        '<p>Please review the identified condition and take appropriate '
        'corrective action if required.</p>',
        '<p>Regards,<br><b>Automated Data QC Monitoring System</b></p>',
        '</div>',
    ])

    return "".join(parts)
