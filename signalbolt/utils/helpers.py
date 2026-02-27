"""
Utility helper functions.

Common utilities used across the application:
- Formatting
- Retries
- File operations
- Validation
"""

import time
import functools
from typing import Optional, Callable, Any, TypeVar, List
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json

from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.utils.helpers')

T = TypeVar('T')


# =============================================================================
# RETRY DECORATOR
# =============================================================================

def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable] = None
):
    """
    Retry decorator with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between retries (seconds)
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch
        on_retry: Callback function(attempt, exception) called on retry
    
    Usage:
        @retry(max_attempts=3, delay=1.0)
        def unstable_function():
            ...
    """
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            current_delay = delay
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        log.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    
                    log.warning(f"{func.__name__} failed (attempt {attempt}/{max_attempts}): {e}")
                    
                    if on_retry:
                        on_retry(attempt, e)
                    
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            raise last_exception
        
        return wrapper
    
    return decorator


# =============================================================================
# TIMING
# =============================================================================

def timeit(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to measure function execution time.
    
    Usage:
        @timeit
        def slow_function():
            ...
    """
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> T:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        
        log.debug(f"{func.__name__} took {elapsed:.3f}s")
        
        return result
    
    return wrapper


class Timer:
    """
    Context manager for timing code blocks.
    
    Usage:
        with Timer("my operation"):
            do_something()
    """
    
    def __init__(self, name: str = "operation", log_level: str = "debug"):
        self.name = name
        self.log_level = log_level
        self.start = None
        self.elapsed = None
    
    def __enter__(self):
        self.start = time.perf_counter()
        return self
    
    def __exit__(self, *args):
        self.elapsed = time.perf_counter() - self.start
        
        msg = f"{self.name} took {self.elapsed:.3f}s"
        
        if self.log_level == "info":
            log.info(msg)
        else:
            log.debug(msg)


# =============================================================================
# FORMATTING
# =============================================================================

def format_usd(value: float, decimals: int = 2) -> str:
    """Format value as USD."""
    
    if abs(value) >= 1_000_000:
        return f"${value/1_000_000:,.{decimals}f}M"
    elif abs(value) >= 1_000:
        return f"${value:,.{decimals}f}"
    else:
        return f"${value:.{decimals}f}"


def format_pct(value: float, decimals: int = 2, with_sign: bool = True) -> str:
    """Format value as percentage."""
    
    if with_sign:
        return f"{value:+.{decimals}f}%"
    return f"{value:.{decimals}f}%"


def format_duration(seconds: float) -> str:
    """Format seconds as human readable duration."""
    
    if seconds < 0:
        return "-" + format_duration(-seconds)
    
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    elif seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f}h"
    else:
        days = seconds / 86400
        return f"{days:.1f}d"


def format_datetime(dt: datetime, include_time: bool = True) -> str:
    """Format datetime."""
    
    if include_time:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def format_number(value: float, decimals: int = 2, compact: bool = False) -> str:
    """Format number with thousand separators."""
    
    if compact:
        if abs(value) >= 1_000_000_000:
            return f"{value/1_000_000_000:.{decimals}f}B"
        elif abs(value) >= 1_000_000:
            return f"{value/1_000_000:.{decimals}f}M"
        elif abs(value) >= 1_000:
            return f"{value/1_000:.{decimals}f}K"
    
    return f"{value:,.{decimals}f}"


def truncate_string(s: str, max_length: int = 50, suffix: str = "...") -> str:
    """Truncate string to max length."""
    
    if len(s) <= max_length:
        return s
    
    return s[:max_length - len(suffix)] + suffix


# =============================================================================
# FILE OPERATIONS
# =============================================================================

def ensure_dir(path: Path) -> Path:
    """Ensure directory exists."""
    
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_write_json(path: Path, data: Any, indent: int = 2) -> bool:
    """
    Safely write JSON file (atomic write).
    
    Writes to temp file first, then renames.
    """
    
    path = Path(path)
    temp_path = path.with_suffix('.tmp')
    
    try:
        ensure_dir(path.parent)
        
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=indent, default=str)
        
        temp_path.rename(path)
        return True
        
    except Exception as e:
        log.error(f"Failed to write {path}: {e}")
        
        if temp_path.exists():
            temp_path.unlink()
        
        return False


def safe_read_json(path: Path, default: Any = None) -> Any:
    """Safely read JSON file."""
    
    path = Path(path)
    
    if not path.exists():
        return default
    
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to read {path}: {e}")
        return default


def get_file_hash(path: Path) -> str:
    """Get MD5 hash of file contents."""
    
    path = Path(path)
    
    if not path.exists():
        return ""
    
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


# =============================================================================
# VALIDATION
# =============================================================================

def validate_symbol(symbol: str) -> bool:
    """Validate trading symbol."""
    
    if not symbol:
        return False
    
    # Must be alphanumeric
    if not symbol.isalnum():
        return False
    
    # Must end with common quote currencies
    valid_quotes = ['USDT', 'BUSD', 'BTC', 'ETH', 'BNB']
    
    return any(symbol.endswith(q) for q in valid_quotes)


def validate_price(price: float) -> bool:
    """Validate price value."""
    
    return isinstance(price, (int, float)) and price > 0


def validate_quantity(quantity: float) -> bool:
    """Validate quantity value."""
    
    return isinstance(quantity, (int, float)) and quantity > 0


def validate_percentage(value: float, min_val: float = 0, max_val: float = 100) -> bool:
    """Validate percentage value."""
    
    return isinstance(value, (int, float)) and min_val <= value <= max_val


# =============================================================================
# MISC
# =============================================================================

def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    """Split list into chunks."""
    
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def flatten_dict(d: dict, parent_key: str = '', sep: str = '.') -> dict:
    """Flatten nested dictionary."""
    
    items = []
    
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    
    return dict(items)


def get_timestamp_str() -> str:
    """Get current timestamp string."""
    
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to range."""
    
    return max(min_val, min(max_val, value))


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """Safe division (returns default on zero division)."""
    
    if b == 0:
        return default
    return a / b


def merge_dicts(*dicts) -> dict:
    """Merge multiple dicts (later overrides earlier)."""
    
    result = {}
    
    for d in dicts:
        if d:
            result.update(d)
    
    return result