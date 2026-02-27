"""
Signal scanner - main scanning loop.

Orchestrates the entire signal discovery and generation pipeline:
1. Discover tradeable coins (CoinDiscovery)
2. Generate signals (SignalGenerator)
3. Validate signals (SignalValidator)
4. Emit valid signals to listeners

Features:
- Configurable scan interval
- Pause/resume functionality
- Regime-aware scanning
- Multiple scan modes
- Event callbacks
- Statistics tracking
- Thread-safe operation

Usage:
    scanner = SignalScanner(exchange, config, strategy)
    
    # Register callback
    scanner.on_signal(lambda s: print(f"New signal: {s.symbol}"))
    
    # Start scanning
    scanner.start()
    
    # ... later
    scanner.pause()
    scanner.resume()
    scanner.stop()
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from queue import Queue, Empty
import traceback

from signalbolt.core.config import Config
from signalbolt.core.strategy import Strategy, Signal
from signalbolt.exchange.base import ExchangeBase
from signalbolt.data.manager import DataManager
from signalbolt.data.price_feed import PriceFeed
from signalbolt.signals.discovery import CoinDiscovery, DiscoveryMode, DiscoveryResult
from signalbolt.signals.generator import SignalGenerator, BatchGenerationResult
from signalbolt.signals.validator import SignalValidator, ValidationResult
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.signals.scanner')


# =============================================================================
# SCANNER STATE
# =============================================================================

class ScannerState(Enum):
    """Scanner state."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class ScanMode(Enum):
    """Scanning mode."""
    CONTINUOUS = "continuous"    # Scan continuously
    SINGLE = "single"            # Single scan
    ON_DEMAND = "on_demand"      # Manual trigger only


# =============================================================================
# SCAN RESULT
# =============================================================================

@dataclass
class ScanResult:
    """Result of single scan cycle."""
    
    scan_id: int
    timestamp: datetime
    
    # Discovery
    discovery_result: Optional[DiscoveryResult] = None
    coins_discovered: int = 0
    
    # Generation
    generation_result: Optional[BatchGenerationResult] = None
    signals_generated: int = 0
    
    # Validation
    signals_validated: int = 0
    signals_rejected: int = 0
    
    # Final output
    valid_signals: List[Signal] = field(default_factory=list)
    validation_results: List[ValidationResult] = field(default_factory=list)
    
    # Timing
    duration_ms: float = 0.0
    discovery_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    validation_time_ms: float = 0.0
    
    # Errors
    errors: List[str] = field(default_factory=list)
    
    @property
    def has_signals(self) -> bool:
        return len(self.valid_signals) > 0
    
    @property
    def best_signal(self) -> Optional[Signal]:
        if not self.valid_signals:
            return None
        return max(self.valid_signals, key=lambda s: s.score)
    
    def to_dict(self) -> dict:
        return {
            'scan_id': self.scan_id,
            'timestamp': self.timestamp.isoformat(),
            'coins_discovered': self.coins_discovered,
            'signals_generated': self.signals_generated,
            'signals_validated': self.signals_validated,
            'signals_rejected': self.signals_rejected,
            'valid_signals': len(self.valid_signals),
            'duration_ms': round(self.duration_ms, 2),
            'errors': self.errors,
        }


# =============================================================================
# SCANNER STATISTICS
# =============================================================================

@dataclass
class ScannerStats:
    """Scanner statistics."""
    
    # Counts
    total_scans: int = 0
    successful_scans: int = 0
    failed_scans: int = 0
    
    # Signals
    total_signals_generated: int = 0
    total_signals_validated: int = 0
    total_signals_rejected: int = 0
    
    # Timing
    total_scan_time_ms: float = 0.0
    avg_scan_time_ms: float = 0.0
    last_scan_time_ms: float = 0.0
    
    # State
    started_at: Optional[datetime] = None
    last_scan_at: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None
    
    # Errors
    total_errors: int = 0
    
    def update(self, result: ScanResult):
        """Update stats with scan result."""
        self.total_scans += 1
        
        if result.errors:
            self.failed_scans += 1
            self.total_errors += len(result.errors)
        else:
            self.successful_scans += 1
        
        self.total_signals_generated += result.signals_generated
        self.total_signals_validated += result.signals_validated
        self.total_signals_rejected += result.signals_rejected
        
        self.total_scan_time_ms += result.duration_ms
        self.last_scan_time_ms = result.duration_ms
        self.avg_scan_time_ms = self.total_scan_time_ms / self.total_scans
        
        self.last_scan_at = result.timestamp
        
        if result.has_signals:
            self.last_signal_at = result.timestamp
    
    def to_dict(self) -> dict:
        return {
            'total_scans': self.total_scans,
            'successful_scans': self.successful_scans,
            'failed_scans': self.failed_scans,
            'total_signals_generated': self.total_signals_generated,
            'total_signals_validated': self.total_signals_validated,
            'total_signals_rejected': self.total_signals_rejected,
            'avg_scan_time_ms': round(self.avg_scan_time_ms, 2),
            'last_scan_time_ms': round(self.last_scan_time_ms, 2),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'last_scan_at': self.last_scan_at.isoformat() if self.last_scan_at else None,
            'last_signal_at': self.last_signal_at.isoformat() if self.last_signal_at else None,
            'uptime_minutes': self._uptime_minutes(),
            'total_errors': self.total_errors,
        }
    
    def _uptime_minutes(self) -> float:
        if not self.started_at:
            return 0.0
        return (datetime.now() - self.started_at).total_seconds() / 60


# =============================================================================
# SIGNAL SCANNER
# =============================================================================

class SignalScanner:
    """
    Main signal scanning engine.
    
    Orchestrates:
    - Coin discovery
    - Signal generation
    - Signal validation
    - Event emission
    
    Usage:
        scanner = SignalScanner(exchange, config, strategy)
        
        # Callbacks
        scanner.on_signal(handle_signal)
        scanner.on_scan_complete(handle_scan)
        
        # Start
        scanner.start()
        
        # Control
        scanner.pause()
        scanner.resume()
        scanner.stop()
        
        # Manual scan
        result = scanner.scan_now()
    """
    
    def __init__(
        self,
        exchange: ExchangeBase,
        config: Config,
        strategy: Strategy,
        data_manager: Optional[DataManager] = None,
        price_feed: Optional[PriceFeed] = None
    ):
        """
        Initialize scanner.
        
        Args:
            exchange: Exchange instance
            config: SignalBolt config
            strategy: Trading strategy
            data_manager: Data manager
            price_feed: Price feed
        """
        self.exchange = exchange
        self.config = config
        self.strategy = strategy
        
        # Components
        self.data_manager = data_manager or DataManager(mode='live', exchange=exchange)
        self.price_feed = price_feed or PriceFeed(exchange)
        
        self.discovery = CoinDiscovery(exchange, config)
        self.generator = SignalGenerator(
            exchange=exchange,
            config=config,
            strategy=strategy,
            data_manager=self.data_manager,
            price_feed=self.price_feed
        )
        self.validator = SignalValidator(
            exchange=exchange,
            config=config,
            price_feed=self.price_feed
        )
        
        # State
        self._state = ScannerState.IDLE
        self._state_lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        
        # Configuration
        self.scan_interval_sec = config.get('scanner', 'scan_interval_sec', default=45)
        self.max_coins_per_scan = config.get('scanner', 'max_coins_per_scan', default=15)
        self.discovery_mode = DiscoveryMode.VOLUME
        self.scan_mode = ScanMode.CONTINUOUS
        
        # Callbacks
        self._on_signal_callbacks: List[Callable[[Signal], None]] = []
        self._on_scan_callbacks: List[Callable[[ScanResult], None]] = []
        self._on_error_callbacks: List[Callable[[Exception], None]] = []
        
        # State tracking
        self._scan_counter = 0
        self._stats = ScannerStats()
        self._recent_signals: List[Signal] = []
        self._max_recent_signals = 100
        
        # Position tracking (set externally)
        self._open_positions: int = 0
        self._open_position_symbols: List[str] = []
        self._wallet_balance: float = 0.0
        
        # Current regime
        self._current_regime: str = 'range'
        
        # Signal queue (for async processing)
        self._signal_queue: Queue[Signal] = Queue()
        
        log.info(f"SignalScanner initialized (interval={self.scan_interval_sec}s, "
                f"max_coins={self.max_coins_per_scan})")
    
    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================
    
    @property
    def state(self) -> ScannerState:
        """Get current scanner state."""
        with self._state_lock:
            return self._state
    
    @property
    def is_running(self) -> bool:
        return self.state == ScannerState.RUNNING
    
    @property
    def is_paused(self) -> bool:
        return self.state == ScannerState.PAUSED
    
    @property
    def is_stopped(self) -> bool:
        return self.state in [ScannerState.STOPPED, ScannerState.IDLE]
    
    def _set_state(self, new_state: ScannerState):
        """Set scanner state."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            log.info(f"Scanner state: {old_state.value} → {new_state.value}")
    
    # =========================================================================
    # CONTROL METHODS
    # =========================================================================
    
    def start(self, mode: ScanMode = ScanMode.CONTINUOUS):
        """
        Start the scanner.
        
        Args:
            mode: Scan mode (continuous, single, on_demand)
        """
        if self.state == ScannerState.RUNNING:
            log.warning("Scanner already running")
            return
        
        self.scan_mode = mode
        self._stop_event.clear()
        self._pause_event.set()
        
        self._stats.started_at = datetime.now()
        
        if mode == ScanMode.CONTINUOUS:
            self._scan_thread = threading.Thread(
                target=self._scan_loop,
                name="SignalScanner",
                daemon=True
            )
            self._scan_thread.start()
            self._set_state(ScannerState.RUNNING)
            log.info("Scanner started (continuous mode)")
        
        elif mode == ScanMode.SINGLE:
            self._set_state(ScannerState.RUNNING)
            result = self._run_scan_cycle()
            self._set_state(ScannerState.STOPPED)
            log.info("Scanner completed single scan")
        
        else:  # ON_DEMAND
            self._set_state(ScannerState.RUNNING)
            log.info("Scanner ready (on-demand mode)")
    
    def stop(self):
        """Stop the scanner."""
        if self.state in [ScannerState.STOPPED, ScannerState.IDLE]:
            return
        
        log.info("Stopping scanner...")
        self._set_state(ScannerState.STOPPING)
        self._stop_event.set()
        self._pause_event.set()  # Unpause to allow thread to exit
        
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=10)
        
        self._set_state(ScannerState.STOPPED)
        log.info("Scanner stopped")
    
    def pause(self):
        """Pause scanning."""
        if self.state != ScannerState.RUNNING:
            return
        
        self._pause_event.clear()
        self._set_state(ScannerState.PAUSED)
        log.info("Scanner paused")
    
    def resume(self):
        """Resume scanning."""
        if self.state != ScannerState.PAUSED:
            return
        
        self._pause_event.set()
        self._set_state(ScannerState.RUNNING)
        log.info("Scanner resumed")
    
    def scan_now(self) -> ScanResult:
        """
        Trigger immediate scan.
        
        Returns:
            ScanResult
        """
        log.info("Manual scan triggered")
        return self._run_scan_cycle()
    
    # =========================================================================
    # MAIN SCAN LOOP
    # =========================================================================
    
    def _scan_loop(self):
        """Main scanning loop (runs in thread)."""
        log.info("Scan loop started")
        
        while not self._stop_event.is_set():
            try:
                # Wait for unpause
                self._pause_event.wait()
                
                if self._stop_event.is_set():
                    break
                
                # Run scan cycle
                self._run_scan_cycle()
                
                # Wait for next interval
                self._wait_interval()
            
            except Exception as e:
                log.error(f"Scan loop error: {e}")
                log.debug(traceback.format_exc())
                self._fire_error_callbacks(e)
                
                # Wait before retry
                time.sleep(5)
        
        log.info("Scan loop ended")
    
    def _wait_interval(self):
        """Wait for scan interval with early exit on stop."""
        interval = self.scan_interval_sec
        check_interval = 1.0  # Check every second
        
        elapsed = 0.0
        while elapsed < interval:
            if self._stop_event.is_set():
                break
            
            time.sleep(check_interval)
            elapsed += check_interval
    
    def _run_scan_cycle(self) -> ScanResult:
        """
        Run single scan cycle.
        
        Returns:
            ScanResult
        """
        self._scan_counter += 1
        scan_id = self._scan_counter
        start_time = datetime.now()
        
        result = ScanResult(
            scan_id=scan_id,
            timestamp=start_time
        )
        
        try:
            # === PHASE 1: DISCOVERY ===
            discovery_start = datetime.now()
            
            discovery_result = self.discovery.discover(
                mode=self.discovery_mode,
                count=self.max_coins_per_scan,
                use_cache=True
            )
            
            result.discovery_result = discovery_result
            result.coins_discovered = len(discovery_result.coins)
            result.discovery_time_ms = (datetime.now() - discovery_start).total_seconds() * 1000
            
            if not discovery_result.coins:
                log.warning(f"Scan #{scan_id}: No coins discovered")
                result.errors.append("No coins discovered")
                return self._finalize_scan(result, start_time)
            
            symbols = discovery_result.symbols
            log.debug(f"Scan #{scan_id}: Discovered {len(symbols)} coins")
            
            # === PHASE 2: GENERATION ===
            generation_start = datetime.now()
            
            generation_result = self.generator.generate_batch(
                symbols=symbols,
                check_cooldown=True,
                current_positions=self._open_positions
            )
            
            result.generation_result = generation_result
            result.signals_generated = len(generation_result.signals)
            result.generation_time_ms = (datetime.now() - generation_start).total_seconds() * 1000
            
            if not generation_result.signals:
                log.debug(f"Scan #{scan_id}: No signals generated")
                return self._finalize_scan(result, start_time)
            
            log.info(f"Scan #{scan_id}: Generated {len(generation_result.signals)} signals")
            
            # === PHASE 3: VALIDATION ===
            validation_start = datetime.now()
            
            for signal in generation_result.signals:
                validation = self.validator.validate(
                    signal=signal,
                    wallet_balance=self._wallet_balance,
                    open_positions=self._open_positions,
                    current_position_symbols=self._open_position_symbols
                )
                
                result.validation_results.append(validation)
                
                if validation.is_valid:
                    result.valid_signals.append(signal)
                    result.signals_validated += 1
                    
                    # Add to recent signals
                    self._add_recent_signal(signal)
                    
                    # Fire callbacks
                    self._fire_signal_callbacks(signal)
                    
                    # Add to queue
                    self._signal_queue.put(signal)
                    
                    log.signal(f"✓ Valid signal: {signal.symbol} {signal.direction} "
                              f"@ {signal.price:.8f} (score: {signal.score:.1f})")
                else:
                    result.signals_rejected += 1
                    log.debug(f"✗ Rejected: {signal.symbol} - {validation.rejection_reason}")
            
            result.validation_time_ms = (datetime.now() - validation_start).total_seconds() * 1000
        
        except Exception as e:
            log.error(f"Scan #{scan_id} error: {e}")
            log.debug(traceback.format_exc())
            result.errors.append(str(e))
            self._fire_error_callbacks(e)
        
        return self._finalize_scan(result, start_time)
    
    def _finalize_scan(self, result: ScanResult, start_time: datetime) -> ScanResult:
        """Finalize scan result."""
        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        # Update stats
        self._stats.update(result)
        
        # Fire callbacks
        self._fire_scan_callbacks(result)
        
        # Log summary
        if result.has_signals:
            best = result.best_signal
            log.info(f"Scan #{result.scan_id} complete: {len(result.valid_signals)} valid signals, "
                    f"best: {best.symbol} ({best.score:.1f}), {result.duration_ms:.0f}ms")
        else:
            log.debug(f"Scan #{result.scan_id} complete: no valid signals, {result.duration_ms:.0f}ms")
        
        return result
    
    # =========================================================================
    # SIGNAL QUEUE
    # =========================================================================
    
    def get_signal(self, timeout: float = 0.0) -> Optional[Signal]:
        """
        Get next signal from queue.
        
        Args:
            timeout: Wait timeout (0 = non-blocking)
        
        Returns:
            Signal or None
        """
        try:
            if timeout > 0:
                return self._signal_queue.get(timeout=timeout)
            else:
                return self._signal_queue.get_nowait()
        except Empty:
            return None
    
    def get_all_signals(self) -> List[Signal]:
        """Get all signals from queue."""
        signals = []
        while True:
            signal = self.get_signal()
            if signal is None:
                break
            signals.append(signal)
        return signals
    
    def clear_signal_queue(self):
        """Clear signal queue."""
        while not self._signal_queue.empty():
            try:
                self._signal_queue.get_nowait()
            except Empty:
                break
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def on_signal(self, callback: Callable[[Signal], None]):
        """
        Register callback for valid signals.
        
        Args:
            callback: Function to call with Signal
        """
        self._on_signal_callbacks.append(callback)
    
    def on_scan_complete(self, callback: Callable[[ScanResult], None]):
        """
        Register callback for scan completion.
        
        Args:
            callback: Function to call with ScanResult
        """
        self._on_scan_callbacks.append(callback)
    
    def on_error(self, callback: Callable[[Exception], None]):
        """
        Register callback for errors.
        
        Args:
            callback: Function to call with Exception
        """
        self._on_error_callbacks.append(callback)
    
    def _fire_signal_callbacks(self, signal: Signal):
        """Fire signal callbacks."""
        for callback in self._on_signal_callbacks:
            try:
                callback(signal)
            except Exception as e:
                log.error(f"Signal callback error: {e}")
    
    def _fire_scan_callbacks(self, result: ScanResult):
        """Fire scan callbacks."""
        for callback in self._on_scan_callbacks:
            try:
                callback(result)
            except Exception as e:
                log.error(f"Scan callback error: {e}")
    
    def _fire_error_callbacks(self, error: Exception):
        """Fire error callbacks."""
        for callback in self._on_error_callbacks:
            try:
                callback(error)
            except Exception as e:
                log.error(f"Error callback error: {e}")
    
    # =========================================================================
    # CONFIGURATION
    # =========================================================================
    
    def set_scan_interval(self, seconds: int):
        """Set scan interval."""
        self.scan_interval_sec = seconds
        log.info(f"Scan interval set to {seconds}s")
    
    def set_max_coins(self, count: int):
        """Set max coins per scan."""
        self.max_coins_per_scan = count
        log.info(f"Max coins per scan set to {count}")
    
    def set_discovery_mode(self, mode: DiscoveryMode):
        """Set discovery mode."""
        self.discovery_mode = mode
        log.info(f"Discovery mode set to {mode.value}")
    
    def set_regime(self, regime: str):
        """
        Set current market regime.
        
        Args:
            regime: 'bull', 'bear', 'range', 'crash'
        """
        self._current_regime = regime
        self.generator.set_regime(regime)
        
        # Adjust discovery mode
        if regime == 'bull':
            self.discovery_mode = DiscoveryMode.MOMENTUM
        elif regime == 'crash':
            self.discovery_mode = DiscoveryMode.VOLUME  # Only most liquid
        else:
            self.discovery_mode = DiscoveryMode.VOLUME
        
        log.info(f"Regime set to {regime}, discovery mode: {self.discovery_mode.value}")
    
    # =========================================================================
    # EXTERNAL STATE
    # =========================================================================
    
    def set_wallet_balance(self, balance: float):
        """Update wallet balance for validation."""
        self._wallet_balance = balance
    
    def set_open_positions(self, count: int, symbols: Optional[List[str]] = None):
        """Update open positions for validation."""
        self._open_positions = count
        self._open_position_symbols = symbols or []
    
    def update_position_state(
        self,
        wallet_balance: float,
        open_positions: int,
        position_symbols: List[str]
    ):
        """Update all position state at once."""
        self._wallet_balance = wallet_balance
        self._open_positions = open_positions
        self._open_position_symbols = position_symbols
    
    # =========================================================================
    # RECENT SIGNALS
    # =========================================================================
    
    def _add_recent_signal(self, signal: Signal):
        """Add signal to recent list."""
        self._recent_signals.append(signal)
        
        # Trim to max
        if len(self._recent_signals) > self._max_recent_signals:
            self._recent_signals = self._recent_signals[-self._max_recent_signals:]
    
    def get_recent_signals(self, limit: int = 20) -> List[Signal]:
        """Get recent valid signals."""
        return self._recent_signals[-limit:]
    
    def get_recent_for_symbol(self, symbol: str, limit: int = 5) -> List[Signal]:
        """Get recent signals for specific symbol."""
        return [s for s in self._recent_signals if s.symbol == symbol][-limit:]
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_stats(self) -> dict:
        """Get scanner statistics."""
        stats = self._stats.to_dict()
        stats['state'] = self.state.value
        stats['scan_mode'] = self.scan_mode.value
        stats['discovery_mode'] = self.discovery_mode.value
        stats['current_regime'] = self._current_regime
        stats['scan_interval_sec'] = self.scan_interval_sec
        stats['max_coins_per_scan'] = self.max_coins_per_scan
        stats['queue_size'] = self._signal_queue.qsize()
        stats['recent_signals'] = len(self._recent_signals)
        
        return stats
    
    def reset_stats(self):
        """Reset statistics."""
        self._stats = ScannerStats()
        self._stats.started_at = datetime.now()
        log.info("Statistics reset")
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def get_discovery(self) -> CoinDiscovery:
        """Get discovery component."""
        return self.discovery
    
    def get_generator(self) -> SignalGenerator:
        """Get generator component."""
        return self.generator
    
    def get_validator(self) -> SignalValidator:
        """Get validator component."""
        return self.validator


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_scanner(
    exchange: ExchangeBase,
    config: Config,
    strategy: Strategy
) -> SignalScanner:
    """Create signal scanner instance."""
    return SignalScanner(exchange, config, strategy)


def quick_scan(
    exchange: ExchangeBase,
    config: Config,
    strategy: Strategy,
    symbols: Optional[List[str]] = None,
    top_n: int = 30
) -> List[Signal]:
    """
    Quick one-time scan.
    
    Args:
        exchange: Exchange instance
        config: Config
        strategy: Strategy
        symbols: Symbols to scan (None = discover)
        top_n: Number of coins to scan
    
    Returns:
        List of valid signals
    """
    scanner = SignalScanner(exchange, config, strategy)
    scanner.set_max_coins(top_n)
    
    result = scanner.scan_now()
    
    return result.valid_signals