"""
Logging setup with level control.

Features:
- Console: minimal / standard / verbose (configurable)
- File: always verbose (full debug info)
- Dual output: both console and file
- JSON logging for machine parsing (optional)
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# LOG LEVELS
# =============================================================================

LOG_LEVELS = {
    'minimal': logging.WARNING,   # Only warnings and errors
    'standard': logging.INFO,     # Info, warnings, errors
    'verbose': logging.DEBUG,     # Everything
}


# =============================================================================
# CUSTOM FORMATTER
# =============================================================================

class ConsoleFormatter(logging.Formatter):
    """
    Console formatter with level-based formatting.
    
    minimal:  [TRADE] BTC/USDT LONG @ $67,234
    standard: [INFO] Signal generated: BTC LONG
    verbose:  [2024-06-15 12:34:56] [DEBUG] EMA9=67234.56
    """
    
    FORMATS = {
        'minimal': "[%(levelname)s] %(message)s",
        'standard': "[%(levelname)s] %(message)s",
        'verbose': "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    }
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'TRADE': '\033[34m',     # Blue
        'SIGNAL': '\033[36m',    # Cyan
        'RESET': '\033[0m',
    }
    
    def __init__(self, level: str = 'standard', use_colors: bool = True):
        self.level = level
        self.use_colors = use_colors
        fmt = self.FORMATS.get(level, self.FORMATS['standard'])
        super().__init__(fmt, datefmt='%Y-%m-%d %H:%M:%S')
    
    def format(self, record):
        # Add colors if enabled
        if self.use_colors and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
            levelname = record.levelname
            color = self.COLORS.get(levelname, '')
            reset = self.COLORS['RESET']
            record.levelname = f"{color}{levelname}{reset}"
        
        return super().format(record)


class FileFormatter(logging.Formatter):
    """
    File formatter - always verbose with full details.
    
    Format: [2024-06-15 12:34:56] [INFO] [signalbolt.core.config] Message here
    """
    
    def __init__(self):
        fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        super().__init__(fmt, datefmt='%Y-%m-%d %H:%M:%S')


class JsonFormatter(logging.Formatter):
    """
    JSON formatter for machine parsing.
    
    Output: {"ts": "2024-06-15T12:34:56", "level": "INFO", "logger": "signalbolt", "msg": "..."}
    """
    
    def format(self, record):
        import json
        
        log_entry = {
            'ts': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, 'data'):
            log_entry['data'] = record.data
        
        return json.dumps(log_entry)


# =============================================================================
# CUSTOM LOG LEVELS
# =============================================================================

# Add custom levels for trading events
TRADE_LEVEL = 25  # Between INFO and WARNING
SIGNAL_LEVEL = 24

logging.addLevelName(TRADE_LEVEL, 'TRADE')
logging.addLevelName(SIGNAL_LEVEL, 'SIGNAL')


def trade(self, message, *args, **kwargs):
    """Log trade event."""
    if self.isEnabledFor(TRADE_LEVEL):
        self._log(TRADE_LEVEL, message, args, **kwargs)


def signal(self, message, *args, **kwargs):
    """Log signal event."""
    if self.isEnabledFor(SIGNAL_LEVEL):
        self._log(SIGNAL_LEVEL, message, args, **kwargs)


# Add methods to Logger class
logging.Logger.trade = trade
logging.Logger.signal = signal


# =============================================================================
# LOGGER SETUP
# =============================================================================

class LoggerManager:
    """
    Manages logging configuration.
    
    Usage:
        # Initialize (call once at startup)
        LoggerManager.setup(console_level='minimal')
        
        # Get logger
        log = LoggerManager.get_logger('signalbolt.core')
        log.info("Hello")
        log.trade("BTC/USDT LONG opened")
    """
    
    _initialized = False
    _console_level = 'standard'
    _loggers = {}
    
    @classmethod
    def setup(
        cls,
        console_level: str = 'standard',
        file_logging: bool = True,
        json_logging: bool = False,
        log_file: str = 'bot.log',
        max_file_size_mb: int = 5,
        backup_count: int = 3
    ):
        """
        Setup logging system.
        
        Args:
            console_level: 'minimal', 'standard', or 'verbose'
            file_logging: Enable file logging (always verbose)
            json_logging: Enable JSON log file
            log_file: Log file name
            max_file_size_mb: Max log file size before rotation
            backup_count: Number of backup files to keep
        """
        cls._console_level = console_level
        
        # Get root logger for signalbolt
        root_logger = logging.getLogger('signalbolt')
        root_logger.setLevel(logging.DEBUG)  # Capture everything
        root_logger.handlers = []  # Clear existing handlers
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(LOG_LEVELS.get(console_level, logging.INFO))
        console_handler.setFormatter(ConsoleFormatter(level=console_level))
        root_logger.addHandler(console_handler)
        
        # File handler (always verbose)
        if file_logging:
            log_path = LOGS_DIR / log_file
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max_file_size_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)  # Always verbose
            file_handler.setFormatter(FileFormatter())
            root_logger.addHandler(file_handler)
        
        # JSON file handler (optional)
        if json_logging:
            json_path = LOGS_DIR / log_file.replace('.log', '.json')
            json_handler = RotatingFileHandler(
                json_path,
                maxBytes=max_file_size_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding='utf-8'
            )
            json_handler.setLevel(logging.DEBUG)
            json_handler.setFormatter(JsonFormatter())
            root_logger.addHandler(json_handler)
        
        # Separate trade log
        trade_logger = logging.getLogger('signalbolt.trades')
        trade_path = LOGS_DIR / 'trades.log'
        trade_handler = RotatingFileHandler(
            trade_path,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding='utf-8'
        )
        trade_handler.setLevel(TRADE_LEVEL)
        trade_handler.setFormatter(FileFormatter())
        trade_logger.addHandler(trade_handler)
        
        # Separate error log
        error_logger = logging.getLogger('signalbolt.errors')
        error_path = LOGS_DIR / 'errors.log'
        error_handler = RotatingFileHandler(
            error_path,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(FileFormatter())
        error_logger.addHandler(error_handler)
        
        cls._initialized = True
        
        # Log startup
        root_logger.info(f"Logger initialized (console: {console_level}, file: {file_logging})")
    
    @classmethod
    def get_logger(cls, name: str = 'signalbolt') -> logging.Logger:
        """
        Get logger instance.
        
        Args:
            name: Logger name (e.g., 'signalbolt.core.config')
        
        Returns:
            Logger instance
        """
        if not cls._initialized:
            cls.setup()
        
        if name not in cls._loggers:
            cls._loggers[name] = logging.getLogger(name)
        
        return cls._loggers[name]
    
    @classmethod
    def set_console_level(cls, level: str):
        """
        Change console log level at runtime.
        
        Args:
            level: 'minimal', 'standard', or 'verbose'
        """
        cls._console_level = level
        
        root_logger = logging.getLogger('signalbolt')
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler):
                handler.setLevel(LOG_LEVELS.get(level, logging.INFO))
                handler.setFormatter(ConsoleFormatter(level=level))
    
    @classmethod
    def get_console_level(cls) -> str:
        """Get current console log level."""
        return cls._console_level


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def setup_logging(console_level: str = 'standard', **kwargs):
    """Setup logging (convenience function)."""
    LoggerManager.setup(console_level=console_level, **kwargs)


def get_logger(name: str = 'signalbolt') -> logging.Logger:
    """Get logger (convenience function)."""
    return LoggerManager.get_logger(name)


# =============================================================================
# QUICK ACCESS LOGGER
# =============================================================================

# Default logger for quick access
log = LoggerManager.get_logger('signalbolt')