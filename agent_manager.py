"""
Keeps one agent running per well in the registry.

The registry is edited over the API while this loop is running, so every pass
compares what should be running against what is, starts what is missing and
stops what has been removed. A well that is added over the API is picked up
within POLL_INTERVAL seconds; nothing has to be restarted.
"""

import threading
import time

import well_registry
from logger import get_logger
from well_agent import WellAgent

log = get_logger(__name__)

# How often to look for wells that have been added or removed.
POLL_INTERVAL = 5


class AgentManager:

    def __init__(self):
        # database_name -> {"agent": WellAgent, "thread": Thread}
        self.agents = {}

    def start(self):
        log.info("Agent manager started")

        while True:
            # A snapshot, because the API thread writes to the registry while
            # this loop reads it.
            wells = well_registry.all_wells()

            self._start_new(wells)
            self._stop_removed(wells)

            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    def _start_new(self, wells):
        for database_name, well in wells.items():

            if database_name in self.agents:
                continue

            log.info("Starting agent: %s", database_name)

            agent = WellAgent(well)

            thread = threading.Thread(
                target=agent.run,
                name=f"agent-{database_name}",
                daemon=True,
            )
            thread.start()

            self.agents[database_name] = {"agent": agent, "thread": thread}

    def _stop_removed(self, wells):
        for database_name in list(self.agents):

            if database_name in wells:
                continue

            log.info("Stopping agent: %s", database_name)

            # stop() only asks; the agent finishes the reading it is in the
            # middle of and closes its own connection on the way out.
            self.agents.pop(database_name)["agent"].stop()
