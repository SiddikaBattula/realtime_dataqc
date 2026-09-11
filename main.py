# from agent_manager import AgentManager


# if __name__ == "__main__":

#     manager = AgentManager()

#     manager.add_wells()

#     manager.start()



import threading
import uvicorn
from config import Config
from config_api import app
from agent_manager import AgentManager


def start_manager():

    manager = AgentManager()

    manager.start()


if __name__ == "__main__":

    threading.Thread(
        target=start_manager,
        daemon=True
    ).start()

    uvicorn.run(
        app,
        host=Config.CONFIG_API_HOST,
        port=Config.CONFIG_API_PORT
    )