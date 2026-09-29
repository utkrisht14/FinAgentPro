import json
import logging
from datetime import datetime, UTC


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
            }
        )

def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])