"""
Error tracking and reporting.

Features:
- Error aggregation
- Rate limiting (avoid spam)
- Error history
- Statistics
- Optional external reporting (Sentry, etc.)
"""

from typing import Optional, Dict, List, Callable, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
import traceback
import threading
import hashlib

from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.utils.error_tracker')


# =============================================================================
# ERROR RECORD
# =============================================================================

@dataclass
class ErrorRecord:
    """Record of an error occurrence."""
    
    error_type: str
    message: str
    timestamp: datetime
    traceback: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    # Aggregation
    count: int = 1
    first_seen: datetime = None
    last_seen: datetime = None
    
    def __post_init__(self):
        if self.first_seen is None:
            self.first_seen = self.timestamp
        if self.last_seen is None:
            self.last_seen = self.timestamp
    
    @property
    def hash(self) -> str:
        """Unique hash for error deduplication."""
        content = f"{self.error_type}:{self.message}"
        return hashlib.md5(content.encode()).hexdigest()[:8]
    
    def increment(self):
        """Increment count and update last_seen."""
        self.count += 1
        self.last_seen = datetime.now()
    
    def to_dict(self) -> dict:
        return {
            'error_type': self.error_type,
            'message': self.message,
            'count': self.count,
            'first_seen': self.first_seen.isoformat(),
            'last_seen': self.last_seen.isoformat(),
            'context': self.context,
        }


# =============================================================================
# ERROR TRACKER
# =============================================================================

class ErrorTracker:
    """
    Tracks and aggregates errors.
    
    Features:
    - Deduplication (same error = increment counter)
    - Rate limiting (max N errors per minute)
    - Error history
    - Statistics
    - Callbacks for alerts
    
    Usage:
        tracker = ErrorTracker()
        
        try:
            risky_operation()
        except Exception as e:
            tracker.track(e, context={'symbol': 'BTCUSDT'})
        
        # Get stats
        tracker.print_summary()
    """
    
    # Singleton instance
    _instance: Optional['ErrorTracker'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize tracker."""
        
        if self._initialized:
            return
        
        # Error storage
        self._errors: Dict[str, ErrorRecord] = {}  # hash -> ErrorRecord
        self._error_history: List[ErrorRecord] = []
        self._max_history = 1000
        
        # Rate limiting
        self._rate_window = timedelta(minutes=1)
        self._rate_limit = 10  # Max errors per minute
        self._recent_errors: List[datetime] = []
        
        # Callbacks
        self._callbacks: List[Callable[[ErrorRecord], None]] = []
        
        # Stats
        self.total_errors = 0
        self.suppressed_errors = 0
        
        self._initialized = True
        
        log.debug("Error tracker initialized")
    
    # =========================================================================
    # TRACKING
    # =========================================================================
    
    def track(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        include_traceback: bool = True
    ) -> Optional[ErrorRecord]:
        """
        Track an error.
        
        Args:
            error: Exception to track
            context: Additional context data
            include_traceback: Whether to include traceback
        
        Returns:
            ErrorRecord if tracked, None if rate limited
        """
        
        self.total_errors += 1
        
        # Check rate limit
        if self._is_rate_limited():
            self.suppressed_errors += 1
            return None
        
        # Create record
        record = ErrorRecord(
            error_type=type(error).__name__,
            message=str(error),
            timestamp=datetime.now(),
            traceback=traceback.format_exc() if include_traceback else None,
            context=context or {}
        )
        
        # Check for duplicate
        error_hash = record.hash
        
        if error_hash in self._errors:
            # Existing error - increment
            existing = self._errors[error_hash]
            existing.increment()
            
            log.debug(f"Error repeated (x{existing.count}): {record.error_type}")
            
            return existing
        
        # New error
        self._errors[error_hash] = record
        self._add_to_history(record)
        
        # Update rate limiter
        self._recent_errors.append(datetime.now())
        
        # Fire callbacks
        for callback in self._callbacks:
            try:
                callback(record)
            except Exception as e:
                log.error(f"Error callback failed: {e}")
        
        log.error(f"New error tracked: {record.error_type}: {record.message}")
        
        return record
    
    def track_exception(
        self,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[ErrorRecord]:
        """
        Track current exception (must be called in except block).
        
        Usage:
            try:
                ...
            except:
                tracker.track_exception(context={'operation': 'fetch_data'})
        """
        
        import sys
        exc_info = sys.exc_info()
        
        if exc_info[1] is None:
            return None
        
        return self.track(exc_info[1], context)
    
    # =========================================================================
    # RATE LIMITING
    # =========================================================================
    
    def _is_rate_limited(self) -> bool:
        """Check if we've hit the rate limit."""
        
        now = datetime.now()
        cutoff = now - self._rate_window
        
        # Remove old entries
        self._recent_errors = [t for t in self._recent_errors if t > cutoff]
        
        return len(self._recent_errors) >= self._rate_limit
    
    def set_rate_limit(self, limit: int, window_minutes: int = 1):
        """Set rate limit parameters."""
        
        self._rate_limit = limit
        self._rate_window = timedelta(minutes=window_minutes)
    
    # =========================================================================
    # HISTORY
    # =========================================================================
    
    def _add_to_history(self, record: ErrorRecord):
        """Add to history with size limit."""
        
        self._error_history.append(record)
        
        if len(self._error_history) > self._max_history:
            self._error_history = self._error_history[-self._max_history:]
    
    def get_history(self, limit: int = 100) -> List[ErrorRecord]:
        """Get recent error history."""
        return self._error_history[-limit:]
    
    def get_unique_errors(self) -> List[ErrorRecord]:
        """Get all unique errors."""
        return list(self._errors.values())
    
    def get_errors_by_type(self, error_type: str) -> List[ErrorRecord]:
        """Get all errors of a specific type."""
        return [e for e in self._errors.values() if e.error_type == error_type]
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def on_error(self, callback: Callable[[ErrorRecord], None]):
        """Register error callback."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable):
        """Remove error callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get error statistics."""
        
        errors_by_type = defaultdict(int)
        
        for record in self._errors.values():
            errors_by_type[record.error_type] += record.count
        
        return {
            'total_errors': self.total_errors,
            'unique_errors': len(self._errors),
            'suppressed_errors': self.suppressed_errors,
            'errors_by_type': dict(errors_by_type),
            'history_size': len(self._error_history),
        }
    
    def print_summary(self):
        """Print error summary."""
        
        stats = self.get_stats()
        
        print(f"\n{'='*60}")
        print(f"ERROR TRACKER SUMMARY")
        print(f"{'='*60}")
        print(f"Total errors:     {stats['total_errors']}")
        print(f"Unique errors:    {stats['unique_errors']}")
        print(f"Suppressed:       {stats['suppressed_errors']}")
        
        if stats['errors_by_type']:
            print(f"\nErrors by type:")
            for error_type, count in sorted(stats['errors_by_type'].items(), 
                                           key=lambda x: x[1], reverse=True):
                print(f"  {error_type}: {count}")
        
        print(f"{'='*60}\n")
    
    def print_recent(self, limit: int = 10):
        """Print recent errors."""
        
        recent = self.get_history(limit)
        
        if not recent:
            print("\nNo errors recorded.\n")
            return
        
        print(f"\n{'='*60}")
        print(f"RECENT ERRORS (last {len(recent)})")
        print(f"{'='*60}")
        
        for record in reversed(recent):
            timestamp = record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{timestamp}] {record.error_type}")
            print(f"  Message: {record.message}")
            if record.count > 1:
                print(f"  Count: {record.count}")
            if record.context:
                print(f"  Context: {record.context}")
        
        print(f"\n{'='*60}\n")
    
    # =========================================================================
    # MANAGEMENT
    # =========================================================================
    
    def clear(self):
        """Clear all tracked errors."""
        
        self._errors.clear()
        self._error_history.clear()
        self._recent_errors.clear()
        self.total_errors = 0
        self.suppressed_errors = 0
        
        log.info("Error tracker cleared")
    
    def export(self) -> List[dict]:
        """Export all errors as list of dicts."""
        
        return [record.to_dict() for record in self._errors.values()]


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

def get_error_tracker() -> ErrorTracker:
    """Get global error tracker instance."""
    return ErrorTracker()


def track_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None
) -> Optional[ErrorRecord]:
    """
    Convenience function to track error.
    
    Usage:
        try:
            ...
        except Exception as e:
            track_error(e, {'symbol': 'BTCUSDT'})
    """
    return get_error_tracker().track(error, context)


# =============================================================================
# DECORATOR
# =============================================================================

def track_errors(context_fn: Optional[Callable] = None):
    """
    Decorator to automatically track errors.
    
    Args:
        context_fn: Optional function to generate context from args
    
    Usage:
        @track_errors()
        def risky_function():
            ...
        
        @track_errors(lambda symbol: {'symbol': symbol})
        def fetch_data(symbol):
            ...
    """
    
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context = {}
                
                if context_fn:
                    try:
                        context = context_fn(*args, **kwargs)
                    except:
                        pass
                
                context['function'] = func.__name__
                track_error(e, context)
                raise
        
        return wrapper
    
    return decorator