"""Make INFO logs visible (uvicorn only configures its own loggers)."""

import logging


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("code_review_agent")
    if root.handlers:
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
