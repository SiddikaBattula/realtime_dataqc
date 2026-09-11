# # agent_manager.py

# import time
# from well_agent import WellAgent


# class AgentManager:

#     def __init__(self):
#         self.agents = {}

#     def start(self):

#         while True:

#             wells = self.load_wells()

#             for well in wells:

#                 if well not in self.agents:

#                     agent = WellAgent(well)

#                     agent.start()

#                     self.agents[well] = agent

#             time.sleep(5)




import threading
import time

from well_agent import WellAgent
from well_registry import ACTIVE_WELLS


class AgentManager:

    def __init__(self):

        self.agents = {}
    def start(self):

        print("Agent Manager Started")

        while True:

            # Start new agents
            for database_name, well in ACTIVE_WELLS.items():

                if database_name not in self.agents:

                    print(f"Starting Agent: {database_name}")

                    agent = WellAgent(well)

                    thread = threading.Thread(
                        target=agent.run,
                        daemon=True
                    )

                    thread.start()

                    self.agents[database_name] = {
                        "agent": agent,
                        "thread": thread
                    }

            # Stop removed agents
            for database_name in list(self.agents.keys()):

                if database_name not in ACTIVE_WELLS:

                    print(f"Stopping Agent: {database_name}")

                    self.agents[database_name]["agent"].stop()

                    del self.agents[database_name]

            time.sleep(5)