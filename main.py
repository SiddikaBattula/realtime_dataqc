"""
Start the whole thing: the config API in the foreground, the agents behind it.

    python main.py

The API is what wells are added to (POST /wells) and what the rule files are
edited through; the agent manager runs in a background thread and starts an
agent for each well the API has been told about. Open /docs to drive both.
"""

import threading

import uvicorn

from agent_manager import AgentManager
from config import Config
from config_api import app
from logger import get_logger, setup_logging

log = get_logger(__name__)


def start_manager():
    AgentManager().start()


def main():
    setup_logging()

    # Daemon, so Ctrl-C on the API brings the agents down with it rather than
    # leaving the process alive with nothing serving.
    threading.Thread(
        target=start_manager,
        name="agent-manager",
        daemon=True,
    ).start()

    log.info(
        "Starting config API on http://%s:%s/docs",
        Config.CONFIG_API_HOST,
        Config.CONFIG_API_PORT,
    )

    uvicorn.run(
        app,
        host=Config.CONFIG_API_HOST,
        port=Config.CONFIG_API_PORT,
        log_config=None,
    )


if __name__ == "__main__":
    main()
