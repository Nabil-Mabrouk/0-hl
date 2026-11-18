"""Worker logging (spec 07-data/03 « Journalisation du worker »).

Attaches one stdout handler to the domain logger `hl` (INFO, UTC timestamps,
no propagation, idempotent). `logging.StreamHandler` flushes after every
record, so lines reach `docker logs` without PYTHONUNBUFFERED.
"""

from __future__ import annotations

import logging
import sys
import time

DOMAIN_LOGGER = "hl"
_MARKER = "_hl_stdout_handler"


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
        return f"{ts}.{int(record.msecs):03d}Z"


def configure_logging(level: int = logging.INFO, stream=None) -> logging.Logger:
    logger = logging.getLogger(DOMAIN_LOGGER)
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers:
        if getattr(handler, _MARKER, False):
            return logger
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(_UtcFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    setattr(handler, _MARKER, True)
    logger.addHandler(handler)
    return logger
