"""
Keeps one agent running per well in the registry.

The registry is edited over the API while this loop is running, so every pass
compares what should be running against what is, starts what is missing and
stops what has been removed. A well that is added over the API is picked up
within AGENT_POLL_INTERVAL seconds (.env); nothing has to be restarted.
"""

import threading
import time

import well_registry
from config import Config
from logger import get_logger
from well_agent import WellAgent

log = get_logger(__name__)


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

            self._collect_dead()
            self._start_new(wells)
            self._stop_removed(wells)

            time.sleep(Config.AGENT_POLL_INTERVAL)

    # ------------------------------------------------------------------
    def _collect_dead(self):
        """
        Report any agent whose thread has ended, and let the next pass restart it.

        run() is not supposed to return while the agent is running, so a dead
        thread means an exception escaped the loop. Nothing used to notice: the
        agent stayed in self.agents forever, so it was never restarted and
        never mentioned again - the well simply stopped being checked and the
        log went quiet, which is indistinguishable from a well with nothing
        wrong. Dropping it here is what makes _start_new pick it up again.
        """
        for database_name in list(self.agents):
            entry = self.agents[database_name]

            if entry["thread"].is_alive() or not entry["agent"].running:
                continue

            log.error(
                "Agent thread for %s has died - restarting it on the next pass",
                database_name,
            )

            self.agents.pop(database_name)

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
