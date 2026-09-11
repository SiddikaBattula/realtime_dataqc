import time
import json

from mysql_client import MySQLClient
from validation_realtime import validate_realtime_data
from logger import setup_logging, get_logger

log = get_logger(__name__)


class MonitoringAgent:

    def __init__(self):

        self.db = MySQLClient()

        self.last_snapshot = None

    def start(self):
        setup_logging()
        log.info("Monitoring Started")

        while True:

            try:

                row = self.db.get_current_row()

                if not row:
                    time.sleep(1)
                    continue

                current_snapshot = json.dumps(
                    row,
                    sort_keys=True,
                    default=str
                )

                if current_snapshot != self.last_snapshot:

                    self.last_snapshot = current_snapshot

                    alerts, spp_pct, totalspm_pct, rop_pct = (
                        validate_realtime_data(row)
                    )

                    # The alerts and the readings behind them are already in
                    # the qc.alerts record; printing them again here is what
                    # put every alert in the terminal twice.
                    for alert in alerts:

                        self.db.save_alert(
                            row["Rdtime"],
                            alert
                        )

                time.sleep(1)

            except Exception as e:

                log.exception("Monitoring loop error: %s", e)

                time.sleep(5)
