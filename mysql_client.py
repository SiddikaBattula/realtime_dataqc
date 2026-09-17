"""
One connection to one rig's database.

The agent builds one of these per well and throws it away whenever a read
fails, so the next pass reconnects rather than looping on a socket that can no
longer answer. Nothing here writes to the rig in normal running:
get_current_row is a SELECT, and save_alert exists for a deployment that wants
the alerts in the database too - the agents do not call it.

Every line is logged against the well that owns the connection: the agent
passes its own logger in, so a timeout in a shared file says which rig timed
out without the message having to carry the name.
"""

import time

import pymysql

from config import Config
from logger import get_logger

_LOG = get_logger(__name__)


class MySQLClient:

    def __init__(self, ip_address, database_name, log=None):
        self.ip_address = ip_address
        self.database_name = database_name

        # The agent's logger when there is one, so the line is filed under the
        # well; this module's own when the client is used on its own.
        self.log = log or _LOG

        started = time.perf_counter()

        self.connection = pymysql.connect(
            host=ip_address,
            user=Config.DB_USERNAME,
            password=Config.DB_PASSWORD,
            port=Config.DB_PORT,
            database=database_name,
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

        self.log.info(
            "connected to %s on %s:%s in %.0fms",
            database_name, ip_address, Config.DB_PORT,
            (time.perf_counter() - started) * 1000,
        )

    def get_current_row(self):
        """The one row the rig keeps its latest reading in, or None."""
        query = f"""
        SELECT *
        FROM {Config.TABLE_NAME}
        LIMIT 1
        """

        started = time.perf_counter()

        with self.connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()

        self.log.debug(
            "read %s: %d column(s) in %.0fms",
            Config.TABLE_NAME,
            len(row) if row else 0,
            (time.perf_counter() - started) * 1000,
        )

        return row

    def save_alert(self, alert_time, description):
        """
        Insert one alert into the rig's own dataqcalert table.

        Not called by the agents - they write to output/<well>/alerts.json and
        leave the rig's database alone.
        """
        query = """
        INSERT INTO dataqcalert
        (
            Time,
            Description
        )
        VALUES
        (
            %s,
            %s
        )
        """

        with self.connection.cursor() as cursor:
            cursor.execute(query, (alert_time, description))

        self.log.info("alert written to dataqcalert: %s", description)

    def close(self):
        """
        Drop the connection. Safe on one the rig has already dropped.

        WellAgent._disconnect calls this from inside its own except handler, so
        it must not raise: an exception here would leave the handler, end
        run(), and kill that well's thread without a word - the agent would
        simply stop checking and nothing would say so.
        """
        try:
            self.connection.close()
            self.log.debug("connection to %s closed", self.database_name)

        except Exception as exc:
            self.log.debug(
                "connection to %s was already gone (%s)", self.database_name, exc
            )
