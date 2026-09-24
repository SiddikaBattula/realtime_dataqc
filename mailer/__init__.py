"""
The ten-minute alert digest.

Every EMAIL_INTERVAL_SECONDS the alerts raised in that time are emailed to the
people responsible for each base region. A window with nothing in it sends
nothing.

    digest.py   which alerts, grouped how, worded how
    sender.py   SMTP, and nothing else
    agent.py    EmailAgent: the thread that decides when

Who each region sends to is not here - it is in config.py with everything else
configurable, read from data/email_config.json.

Not named "email". Python's own standard library has a package of that name,
and a folder called email/ beside main.py is found first - which breaks
smtplib, because the first thing smtplib does is "import email.utils". The
failure is ModuleNotFoundError: No module named 'email.utils', from a file
nobody touched.

Everything here reads: output/<well>/alerts.json, data/email_config.json and
the well registry. It never opens a rig's database, so a well that cannot be
reached can neither slow this down nor stop it.
"""

from mailer.agent import EmailAgent, start, send_test
from mailer.sender import EmailError, configured

__all__ = [
    "EmailAgent",
    "EmailError",
    "configured",
    "send_test",
    "start",
]
