
import threading

import uvicorn

import mailer

from agent_manager import AgentManager
from config import Config
from config_api import app
from logger import get_logger, setup_logging

log = get_logger(__name__)


def start_manager():
    AgentManager().start()


def main():
    setup_logging()

    threading.Thread(
        target=start_manager,
        name="agent-manager",
        daemon=True,
    ).start()

    # Its own thread, so a mail server that has stopped answering delays the
    # digest and nothing else. It returns immediately when email is not set up
    # in .env, which is what keeps a machine with no SMTP running as before.
    threading.Thread(
        target=mailer.start,
        name="email-digest",
        daemon=True,
    ).start()

    log.info("%s:%s", Config.CONFIG_API_HOST, Config.CONFIG_API_PORT)

    uvicorn.run(
        app,
        host=Config.CONFIG_API_HOST,
        port=Config.CONFIG_API_PORT,
        log_config=None,
    )


if __name__ == "__main__":
    main()
