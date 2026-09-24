
import os
import threading

import uvicorn

import mailer

from agent_manager import AgentManager
from config import Config
from config_api import app
from logger import get_logger, setup_logging

log = get_logger(__name__)

# Values of FAKE_RIG that mean "yes". Anything else, including it not being
# set at all, is the normal run against the real rigs.
_YES = {"1", "true", "yes", "on"}


def start_manager():
    AgentManager().start()


def use_fake_rig():
    """
    Whether to feed the agents simulated readings instead of reading a rig.

    For working on the dashboard and the checks while no rig is reachable -
    fake_rig.py serves each well rows built from its own rules, and nothing
    about the rest of the app changes. It is off unless FAKE_RIG says so, and
    importing fake_rig is what turns it on, so an ordinary run never touches
    it.
    """
    if os.getenv("FAKE_RIG", "").strip().lower() not in _YES:
        return False

    import fake_rig

    fake_rig.attach()

    return True


def main():
    setup_logging()

    use_fake_rig()

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
