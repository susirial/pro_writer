import logging
import sys
from pathlib import Path
from core.config import config

def setup_logger():
    logger = logging.getLogger("novel_agent_system")
    
    if logger.handlers:
        return logger

    log_level_str = config.get("system.log_level", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File Handler
    log_file_path = config.get("system.log_file", "logs/system.log")
    base_dir = Path(__file__).resolve().parent.parent
    log_file = base_dir / log_file_path
    
    # Ensure logs directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(log_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

logger = setup_logger()
