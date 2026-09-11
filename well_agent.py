#well_agent.py

from mysql_client import MySQLClient

import time
import json

from pathlib import Path

from mysql_client import MySQLClient
from validation_realtime import build_validator


class WellAgent:

    def __init__(self, well):

        self.database_name = well["database_name"]

        self.ip_address = well["ip_address"]

        self.db = None

        self.validator = None

        self.last_snapshot = None 

        self.running = True
          

    def run(self):

        print(f"Agent Started: {self.database_name}")

        while self.running:

            try:

                if self.db is None:

                    self.db = MySQLClient(
                        self.ip_address,
                        self.database_name
                    )
                print(f"[{self.database_name}] Checking ")
                row = self.db.get_current_row()

                if not row:
                    time.sleep(1)
                    continue

                if self.validator is None:
                    self.validator = build_validator(
                        row
                    )

                if self.db is None:
                    self.db = MySQLClient(
                        self.ip_address,
                        self.database_name
                    )


                snapshot = json.dumps(
                    row,
                    sort_keys=True,
                    default=str
                )

                if snapshot != self.last_snapshot:

                    self.last_snapshot = snapshot

                    alerts, spp, spm, rop = (
                        self.validator.validate_realtime_data(row)
                    )

                    self.save_alerts(alerts)

                time.sleep(1)

            except Exception as e:

                print(
                    f"{self.database_name}: {e}"
                )

                time.sleep(5)

    def save_alerts(self, alerts):

        if not alerts:
            return

        file_path = (
            Path("output")
            / self.database_name
            / "alerts.json"
        )

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        try:

            if file_path.exists():

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    existing = json.load(f)

            else:

                existing = []

            existing.extend(alerts)

            with open(
                file_path,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    existing,
                    f,
                    indent=4
                )

        except Exception as e:

            print(
                f"Alert save error: {e}"
            )

    def stop(self):

        self.running = False

        print(f"Agent Stopped: {self.database_name}")