"""
How long a zero-value or bit-depth-jump condition lasted.

Both alerts are saved once, when they start: a reading of 0 that stays 0 is
the same alert on every reading, so raise_alert keeps it out of the file after
the first. That leaves the file - and the report built from it - knowing when
the problem began but never when it ended.

The data feed check already closes its own alerts ("Realtime data feed has
resumed ... for N seconds"). This does the same for the other two: it notes
the first and last reading each subject was raised on, and once it has gone
CLOSE_AFTER_SECONDS without being raised, writes one closing alert carrying
both times:

    [10-10-26 14-22-41] RPM was 0 in DRILLING from 10-10-26 14:20:03
        to 10-10-26 14:22:10 (127s), now 60.00, BD:2499.95m
    [10-10-26 14-32-14] Bit depth was jumping from 10-10-26 14:31:19
        to 10-10-26 14:31:44 (25s), 3 jump(s), largest 8.01m,
        now steady, BD:2499.95m

The times inside use ":" so they cannot be mistaken for the "[...]" stamp
every alert starts with.

Why wait before closing: a jumpy bit depth raises on one reading, is quiet for
a few, then raises again. Closing on the first quiet reading would write a
closing alert per jump - doubling the alerts - and report a run of jumps as
dozens of 0-second episodes instead of one stretch. The span reported is still
first-raised to last-raised; the wait only delays when it is written.

Episodes still open when the agent stops or loses the rig are not closed:
nothing is known about how they ended.
"""

from .constants import ALERT_TIME_FORMAT

# The subjects whose episodes are closed here. ZERO:<param> for every
# activity zero-check, and the bit-depth jump.
ZERO_PREFIX = "ZERO:"
BIT_DEPTH_SUBJECT = "BIT_DEPTH_CHANGE"

# The from/to times written inside the sentence.
SPAN_TIME_FORMAT = "%d-%m-%y %H:%M:%S"

# How long a subject must go unraised before its episode is closed. Raise it
# to merge jumps that are further apart into one episode.
CLOSE_AFTER_SECONDS = 30


def _tracked(subject):
    return subject.startswith(ZERO_PREFIX) or subject == BIT_DEPTH_SUBJECT


class EpisodeTracker:

    def __init__(self):
        # subject -> {"start", "last", "readings", "activity", "largest"}
        self.open = {}

    def reset(self):
        self.open.clear()

    def note(self, subject, now, activity=None, jump=None):
        """This reading raised `subject`: start its episode or extend it."""
        if not _tracked(subject):
            return

        episode = self.open.get(subject)

        if episode is None:
            episode = self.open[subject] = {
                "start": now,
                "last": now,
                "readings": 0,
                "activity": activity,
                "largest": 0.0,
            }

        episode["last"] = now
        episode["readings"] += 1

        if jump is not None:
            episode["largest"] = max(episode["largest"], jump)

    def close_cleared(self, validator, raised, raise_alert, now, normalized_data,
                      bit_depth, depth_unit):
        """One closing alert per episode quiet for CLOSE_AFTER_SECONDS."""
        for subject in list(self.open):
            episode = self.open[subject]

            if subject in raised:
                continue

            if (now - episode["last"]).total_seconds() < CLOSE_AFTER_SECONDS:
                continue

            del self.open[subject]

            start = episode["start"]
            last = episode["last"]
            seconds = int((last - start).total_seconds())

            span = (
                f"from {start.strftime(SPAN_TIME_FORMAT)} "
                f"to {last.strftime(SPAN_TIME_FORMAT)} ({seconds}s)"
            )

            if subject == BIT_DEPTH_SUBJECT:
                message = (
                    f"Bit depth was jumping {span}, "
                    f"{episode['readings']} jump(s), "
                    f"largest {episode['largest']:.2f}{depth_unit}, now steady"
                )
                params = ("BIT_DPT_MD",)
            else:
                param = subject[len(ZERO_PREFIX):]
                value = (
                    validator._get_total_spm(normalized_data) if param == "SPM"
                    else normalized_data.get(param)
                )
                reading = f"now {value:.2f}" if value is not None else "now no reading"
                message = (
                    f"{validator.display_name(param)} was 0 in "
                    f"{episode['activity'] or 'unknown activity'} {span}, {reading}"
                )
                params = (param,)

            if bit_depth is not None:
                message += f", BD:{bit_depth:.2f}{depth_unit}"

            validator.log.info(
                "%s CLEARED | %s | %d reading(s)", subject, span, episode["readings"],
            )

            raise_alert(
                f"[{now.strftime(ALERT_TIME_FORMAT)}] {message}",
                *params,
                subject=f"CLEARED:{subject}",
                value=start.timestamp(),
                why=(
                    f"{subject} raised on {episode['readings']} reading(s) {span}, "
                    f"and not in the {CLOSE_AFTER_SECONDS}s since"
                ),
            )
