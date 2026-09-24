"""
Putting a message on the wire.

Nothing but SMTP, so the digest can be built and read without a mail server
and this can be pointed at one without a rig. Everything it needs is in .env -
see EMAIL_* in config.py - because credentials belong on the machine and not
in a file that is committed.

smtplib and email are both standard library: no dependency is added for this.
"""

import smtplib
import ssl

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from config import Config
from logger import get_logger

log = get_logger(__name__)


class EmailError(RuntimeError):
    """The message did not go out, with a sentence saying why."""


def configured():
    """
    Whether there is enough in .env to send anything.

    Checked before the thread starts and again before each send, so a system
    with no mail server set up runs exactly as it did rather than logging a
    failure every ten minutes.
    """
    return bool(Config.EMAIL_ENABLED and Config.SMTP_HOST and Config.SMTP_FROM)


def _connection():
    """
    An open, ready-to-send SMTP connection.

    Two ways in. SMTP_SSL wraps the socket in TLS from the first byte, which
    is what port 465 expects; plain SMTP connects in the clear and then
    upgrades with STARTTLS, which is what 587 expects. Getting the two the
    wrong way round is the usual cause of a connection that hangs until it
    times out rather than refusing outright, so it is set in .env instead of
    being guessed from the port.
    """
    timeout = Config.SMTP_TIMEOUT

    if Config.SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(
            Config.SMTP_HOST,
            Config.SMTP_PORT,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=timeout)

        if Config.SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())

    # An open relay on the rig network needs no login, and passing empty
    # credentials to one that does not want them is itself an error.
    if Config.SMTP_USERNAME:
        server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD or "")

    return server


def _compose(to, subject, text, html):
    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = formataddr((Config.EMAIL_FROM_NAME, Config.SMTP_FROM))
    message["To"] = ", ".join(to)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()

    # An alert digest is a notice, not a conversation. Without this an out of
    # office reply comes back to the sending mailbox every ten minutes, and on
    # a bad day two systems answer each other all night.
    message["Auto-Submitted"] = "auto-generated"
    message["X-Auto-Response-Suppress"] = "All"

    message.set_content(text)

    if html:
        message.add_alternative(html, subtype="html")

    return message


def send(to, subject, text, html=None):
    """
    Send one message. Raises EmailError if it did not go.

    Raising rather than returning False is deliberate: the caller decides
    whether the window is marked as sent, and a send that quietly failed would
    lose ten minutes of alerts with nothing in the log to say so.
    """
    recipients = [address for address in to if address]

    if not recipients:
        raise EmailError("No recipients")

    if not configured():
        raise EmailError(
            "Email is not configured - set EMAIL_ENABLED, SMTP_HOST and "
            "SMTP_FROM in .env"
        )

    try:
        with _connection() as server:
            server.send_message(_compose(recipients, subject, text, html))

    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise EmailError(
            "{} via {}:{} - {}".format(
                type(exc).__name__, Config.SMTP_HOST, Config.SMTP_PORT, exc
            )
        ) from exc

    log.info("Sent %r to %s", subject, ", ".join(recipients))

    return recipients
