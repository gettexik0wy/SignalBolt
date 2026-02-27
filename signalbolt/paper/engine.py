"""
Paper trading engine - main orchestrator.

Combines all components:
- Session management (save/load/replay)
- Signal scanning
- Trade execution
- Position management
- Config hot-reload
- Statistics

Usage:
    engine = PaperEngine(config_name)
    engine.start_session("my_session", create_new=True)
    engine.run()
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Any
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import traceback

from signalbolt.paper.replay_engine import ReplayEngine, replay_offline_period
from signalbolt.core.config import Config
from signalbolt.paper.portfolio import PaperPortfolio, Position, TradeResult
from signalbolt.paper.session import PaperSession, SessionStatus
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.paper.engine")


# =============================================================================
# ENGINE STATE
# =============================================================================


class EngineState(Enum):
    """Engine state."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    REPLAYING = "replaying"
    ERROR = "error"


# =============================================================================
# ENGINE EVENTS
# =============================================================================


@dataclass
class EngineEvent:
    """Engine event for callbacks."""

    event_type: str
    timestamp: datetime
    data: Dict[str, Any]


class EngineEventType:
    """Event type constants."""

    STARTED = "started"
    STOPPED = "stopped"
    PAUSED = "paused"
    RESUMED = "resumed"
    SCAN_COMPLETE = "scan_complete"
    SIGNAL_GENERATED = "signal_generated"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    REPLAY_STARTED = "replay_started"
    REPLAY_COMPLETE = "replay_complete"
    CONFIG_RELOADED = "config_reloaded"
    ERROR = "error"


# =============================================================================
# PAPER ENGINE
# =============================================================================


class PaperEngine:
    """
    Main paper trading engine.

    Orchestrates:
    - Session management
    - Signal scanning
    - Trade execution
    - Position management
    - Config hot-reload

    Usage:
        engine = PaperEngine("config_safe.yaml")
        engine.start_session("test", create_new=True, initial_balance=1000)
        engine.run()
    """

    def __init__(self, config_name: Optional[str] = None):
        """
        Initialize engine.

        Args:
            config_name: Config file name (e.g., 'config_safe.yaml')
        """
        self.config_name = config_name or "config_safe.yaml"
        self.config: Optional[Config] = None

        # State
        self._state = EngineState.IDLE
        self._state_lock = threading.Lock()

        # Session
        self._session: Optional[PaperSession] = None

        # Components (initialized on session load)
        self._exchange = None
        self._strategy = None
        self._data_manager = None
        self._price_feed = None
        self._scanner = None
        self._executor = None

        # Threading
        self._engine_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused

        # Config tracking
        self._config_last_check = datetime.now()
        self._config_check_interval = timedelta(seconds=10)

        # Statistics
        self._scan_count = 0
        self._signal_count = 0
        self._trade_count = 0
        self._start_time: Optional[datetime] = None
        self._last_scan_time: Optional[datetime] = None

        # Callbacks
        self._event_callbacks: List[Callable[[EngineEvent], None]] = []

        # Current regime
        self._current_regime = "range"

        log.info("PaperEngine initialized")

    # =========================================================================
    # STATE
    # =========================================================================

    @property
    def state(self) -> EngineState:
        """Get current engine state."""
        with self._state_lock:
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state == EngineState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self.state == EngineState.PAUSED

    def _set_state(self, new_state: EngineState):
        """Set engine state."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            log.info(f"Engine state: {old_state.value} -> {new_state.value}")

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    def start_session(
        self,
        session_name: str,
        create_new: bool = False,
        initial_balance: float = 1000.0,
    ) -> bool:
        """
        Start a paper trading session.

        Args:
            session_name: Session name or ID
            create_new: If True, create new session. If False, load existing.
            initial_balance: Starting balance (only for new sessions)

        Returns:
            True if session started successfully
        """
        try:
            # Load config
            Config.reset()
            self.config = Config(mode="paper", config_name=self.config_name)

            if create_new:
                log.info(f"Creating new session: {session_name}")
                self._session = PaperSession.create(
                    name=session_name,
                    initial_balance=initial_balance,
                    config=self.config,
                    config_name=self.config_name,
                )
            else:
                log.info(f"Loading session: {session_name}")
                self._session = PaperSession.load(session_name, self.config)

            # Initialize components FIRST (needed for replay)
            self._initialize_components()

            # Check for offline replay AFTER components are ready
            if self._session.needs_replay():
                self._run_replay()

            return True

        except Exception as e:
            log.error(f"Failed to start session: {e}")
            log.debug(traceback.format_exc())
            return False

    def _run_replay(self) -> bool:
        """
        Run offline replay if needed.

        Returns:
            True if replay was run
        """
        if not self._session or not self._session.needs_replay():
            return False

        if not self._strategy:
            log.warning("Cannot run replay: no strategy loaded")
            self._session.mark_replay_complete()
            return False

        print(f"\n{'=' * 60}")
        print(f"⚠️  OFFLINE PERIOD DETECTED")
        print(f"{'=' * 60}")

        start, end = self._session.get_replay_period()
        duration_hours = (end - start).total_seconds() / 3600

        print(
            f"\n  Period: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}"
        )
        print(f"  Duration: {duration_hours:.1f} hours")
        print()

        # Ask user
        response = input("  Run replay simulation? [Y/n]: ").strip().lower()

        if response in ("n", "no"):
            print("  Skipping replay...")
            self._session.mark_replay_complete()
            return False

        print("\n  🔄 Starting replay...\n")

        # Get symbols
        symbols = self._session.get_watched_symbols()

        if not symbols:
            # Get from discovery if available
            try:
                from signalbolt.signals.discovery import CoinDiscovery

                discovery = CoinDiscovery(self._exchange, self.config)
                symbols = discovery.get_top_coins(
                    self.config.get("discovery", "top_coins", default=30)
                )
                self._session.set_watched_symbols(symbols)
            except Exception as e:
                log.warning(f"Could not discover coins: {e}")
                symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

        # Run replay
        replay_engine = ReplayEngine(
            session=self._session,
            strategy=self._strategy,
            config=self.config,
            data_manager=self._data_manager,
        )

        result = replay_engine.run(
            start_time=start, end_time=end, symbols=symbols, verbose=True
        )

        # Print summary
        print(result.summary())

        # Mark complete
        self._session.mark_replay_complete()

        input("\n  Press Enter to continue...")

        return True

    def _initialize_components(self):
        """Initialize trading components."""
        if not self._session:
            return

        try:
            # Initialize exchange
            from signalbolt.exchange.client import get_exchange

            self._exchange = get_exchange(
                exchange_name=self.config.EXCHANGE_NAME,
                testnet=self.config.get("exchange", "testnet", default=False),
            )

            # Initialize strategy
            self._load_strategy()

            # Initialize data manager
            from signalbolt.data.manager import DataManager

            self._data_manager = DataManager(
                mode="live", exchange=self._exchange, verbose=False
            )

            # Initialize price feed
            from signalbolt.data.price_feed import PriceFeed

            self._price_feed = PriceFeed(self._exchange)

            # Initialize scanner
            from signalbolt.signals.scanner import SignalScanner

            self._scanner = SignalScanner(
                exchange=self._exchange,
                config=self.config,
                strategy=self._strategy,
                data_manager=self._data_manager,
                price_feed=self._price_feed,
            )

            # Initialize executor
            from signalbolt.paper.executor import PaperExecutor

            self._executor = PaperExecutor(
                portfolio=self._session.portfolio,
                config=self.config,
                price_feed=self._price_feed,
                slippage_enabled=True,
            )

            log.info("Components initialized")

        except ImportError as e:
            log.warning(f"Some components not available: {e}")
        except Exception as e:
            log.error(f"Failed to initialize components: {e}")

    def _load_strategy(self):
        """Load strategy from config or default."""
        strategy_name = self.config.get(
            "strategy", "name", default="SignalBoltOriginal"
        )

        try:
            # Direct import for known strategies
            from signalbolt.strategies.SignalBolt_original import SignalBoltOriginal

            if strategy_name in ["SignalBoltOriginal", "SignalBolt_original"]:
                self._strategy = SignalBoltOriginal(self.config)
                log.info("Loaded SignalBoltOriginal strategy")
            else:
                # Try dynamic loading
                from signalbolt.core.strategy import create_strategy

                self._strategy = create_strategy(strategy_name, self.config)

        except Exception as e:
            log.error(f"Failed to load strategy: {e}")
            self._strategy = None

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    def run(self):
        """
        Run engine (blocking).

        This is the main entry point for paper trading.
        Runs until stop() is called.
        """
        if not self._session:
            raise RuntimeError("No session loaded. Call start_session() first.")

        self._start()

        try:
            self._main_loop()
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        finally:
            self._stop()

    def start(self):
        """Start engine in background thread."""
        if not self._session:
            raise RuntimeError("No session loaded")

        self._engine_thread = threading.Thread(
            target=self._run_threaded, name="PaperEngine", daemon=True
        )
        self._engine_thread.start()

    def _run_threaded(self):
        """Thread entry point."""
        self._start()

        try:
            self._main_loop()
        except Exception as e:
            log.error(f"Engine error: {e}")
            self._set_state(EngineState.ERROR)
        finally:
            self._stop()

    def _start(self):
        """Initialize and start."""
        self._set_state(EngineState.STARTING)

        self._stop_event.clear()
        self._pause_event.set()

        self._start_time = datetime.now()
        self._scan_count = 0
        self._signal_count = 0
        self._trade_count = 0

        # Start session
        self._session.start()

        self._set_state(EngineState.RUNNING)

        self._emit_event(
            EngineEventType.STARTED,
            {
                "session_id": self._session.session_id,
                "balance": self._session.portfolio.total_balance,
            },
        )

        log.info(f"Engine started: {self._session.session_id}")

    def _main_loop(self):
        """Main trading loop."""
        scan_interval = self.config.get("scanner", "scan_interval_sec", default=45)

        log.info(f"Starting main loop (interval: {scan_interval}s)")

        while not self._stop_event.is_set():
            try:
                # Wait for unpause
                self._pause_event.wait()

                if self._stop_event.is_set():
                    break

                # === SCAN CYCLE ===
                cycle_start = datetime.now()
                self._last_scan_time = cycle_start

                # 1. Get current prices for open positions
                prices = self._get_current_prices()

                # 2. Check exits on open positions
                if prices and self._executor:
                    try:
                        exits = self._executor.check_exits(prices)
                        for trade_result in exits:
                            self._trade_count += 1
                    except Exception as e:
                        log.debug(f"Exit check error: {e}")

                # 3. Run signal scan
                if self._scanner:
                    try:
                        # Update position state
                        portfolio = self._session.portfolio
                        self._scanner.update_position_state(
                            wallet_balance=portfolio.quote_balance,
                            open_positions=portfolio.open_position_count,
                            position_symbols=portfolio.open_symbols,
                        )

                        scan_result = self._scanner.scan_now()
                        self._scan_count += 1

                        # Execute valid signals
                        for signal in scan_result.valid_signals:
                            self._execute_signal(signal)
                    except Exception as e:
                        log.debug(f"Scan error: {e}")

                # 4. Save checkpoint
                self._session.save_checkpoint()

                # 5. Check config reload
                self._check_config_reload()

                # Print status
                self._print_cycle_status()

                # === WAIT FOR NEXT CYCLE ===
                cycle_duration = (datetime.now() - cycle_start).total_seconds()
                wait_time = max(0, scan_interval - cycle_duration)

                self._interruptible_sleep(wait_time)

            except Exception as e:
                log.error(f"Loop error: {e}")
                log.debug(traceback.format_exc())

                self._emit_event(EngineEventType.ERROR, {"error": str(e)})

                # Wait before retry
                self._interruptible_sleep(5)

        log.info("Main loop ended")

    def _print_cycle_status(self):
        """Print status after each cycle."""
        if not self._session:
            return

        portfolio = self._session.portfolio

        print(
            f"\r  [Scan #{self._scan_count}] "
            f"Balance: ${portfolio.total_balance:.2f} | "
            f"Positions: {portfolio.open_position_count} | "
            f"Trades: {self._trade_count} | "
            f"Signals: {self._signal_count}   ",
            end="",
            flush=True,
        )

    def _stop(self):
        """Clean shutdown."""
        self._set_state(EngineState.STOPPING)

        print()  # New line after status

        # Stop session
        if self._session:
            self._session.pause()
            self._session.save_checkpoint()

        self._set_state(EngineState.STOPPED)

        self._emit_event(
            EngineEventType.STOPPED,
            {
                "total_scans": self._scan_count,
                "total_signals": self._signal_count,
                "total_trades": self._trade_count,
            },
        )

        log.info("Engine stopped")

    def _interruptible_sleep(self, seconds: float):
        """Sleep that can be interrupted by stop event."""
        interval = 0.5
        elapsed = 0.0

        while elapsed < seconds:
            if self._stop_event.is_set():
                break

            time.sleep(min(interval, seconds - elapsed))
            elapsed += interval

    # =========================================================================
    # CONTROL
    # =========================================================================

    def stop(self):
        """Stop the engine."""
        log.info("Stop requested")
        self._stop_event.set()
        self._pause_event.set()  # Unpause to allow exit

        if self._engine_thread and self._engine_thread.is_alive():
            self._engine_thread.join(timeout=10)

    def pause(self):
        """Pause the engine."""
        if self.state != EngineState.RUNNING:
            return

        self._pause_event.clear()
        self._set_state(EngineState.PAUSED)

        if self._session:
            self._session.pause()

        self._emit_event(EngineEventType.PAUSED, {})

        log.info("Engine paused")

    def resume(self):
        """Resume the engine."""
        if self.state != EngineState.PAUSED:
            return

        self._pause_event.set()
        self._set_state(EngineState.RUNNING)

        if self._session:
            self._session.start()

        self._emit_event(EngineEventType.RESUMED, {})

        log.info("Engine resumed")

    # =========================================================================
    # SIGNAL EXECUTION
    # =========================================================================

    def _execute_signal(self, signal):
        """Execute a valid signal."""
        if not self._executor:
            return

        # Get current price
        current_price = None
        if self._price_feed:
            ticker = self._price_feed.get_ticker(signal.symbol)
            if ticker:
                current_price = ticker.last_price

        # Execute (without strategy parameter)
        result = self._executor.execute_signal(
            signal=signal, current_price=current_price
        )

        if result.success:
            self._signal_count += 1

            # Add to watched symbols
            if self._session:
                self._session.add_watched_symbol(signal.symbol)

    # =========================================================================
    # CONFIG HOT-RELOAD
    # =========================================================================

    def _check_config_reload(self):
        """Check if config needs reload."""
        now = datetime.now()

        if now - self._config_last_check < self._config_check_interval:
            return

        self._config_last_check = now

        # Check for pending reload
        if self.config and self.config.reload_pending:
            log.info("Config reload detected")

            if self.config.do_reload():
                self._emit_event(
                    EngineEventType.CONFIG_RELOADED, {"warnings": self.config.warnings}
                )

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_current_prices(self) -> Dict[str, float]:
        """Get current prices for open positions."""
        if not self._session or not self._price_feed:
            return {}

        symbols = self._session.portfolio.open_symbols

        if not symbols:
            return {}

        return self._price_feed.get_prices(symbols)

    def close_all_positions(self, reason: str = "manual") -> List[TradeResult]:
        """Close all open positions."""
        results = []

        if not self._session or not self._executor:
            return results

        prices = self._get_current_prices()

        for position in self._session.portfolio.open_positions:
            if position.symbol in prices:
                try:
                    result = self._executor.close_position(
                        position, prices[position.symbol], reason
                    )
                    if result:
                        results.append(result)
                except Exception as e:
                    log.error(f"Failed to close {position.symbol}: {e}")

        return results

    # =========================================================================
    # CALLBACKS
    # =========================================================================

    def on_event(self, callback: Callable[[EngineEvent], None]):
        """Register event callback."""
        self._event_callbacks.append(callback)

    def _emit_event(self, event_type: str, data: Dict):
        """Emit engine event."""
        event = EngineEvent(event_type=event_type, timestamp=datetime.now(), data=data)

        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                log.error(f"Event callback error: {e}")

    # =========================================================================
    # STATUS & STATISTICS
    # =========================================================================

    def get_status(self) -> dict:
        """Get current engine status."""
        runtime = 0.0
        if self._start_time:
            runtime = (datetime.now() - self._start_time).total_seconds() / 60

        portfolio_data = {}
        session_name = None
        pnl_pct = 0.0

        if self._session:
            session_name = self._session.name
            portfolio = self._session.portfolio
            portfolio_data = {
                "balance": portfolio.total_balance,
                "quote_balance": portfolio.quote_balance,
                "unrealized_pnl": portfolio.unrealized_pnl,
                "open_positions": portfolio.open_position_count,
            }

            # Calculate P&L %
            initial = portfolio.initial_balance
            if initial > 0:
                pnl_pct = ((portfolio.total_balance - initial) / initial) * 100

        return {
            "state": self.state.value,
            "session": session_name,
            "runtime_minutes": round(runtime, 1),
            "scan_count": self._scan_count,
            "signal_count": self._signal_count,
            "trade_count": self._trade_count,
            "pnl_pct": pnl_pct,
            "portfolio": portfolio_data,
            "last_scan": self._last_scan_time.isoformat()
            if self._last_scan_time
            else None,
        }

    def print_status(self):
        """Print formatted status."""
        status = self.get_status()

        print(f"\n{'=' * 60}")
        print(f"{'ENGINE STATUS':^60}")
        print(f"{'=' * 60}")

        print(f"\n  State:      {status['state'].upper()}")
        print(f"  Session:    {status['session'] or 'None'}")
        print(f"  Runtime:    {status['runtime_minutes']:.1f} minutes")
        print(f"  Scans:      {status['scan_count']}")
        print(f"  Signals:    {status['signal_count']}")
        print(f"  Trades:     {status['trade_count']}")

        if status["portfolio"]:
            p = status["portfolio"]
            print(f"\n  {'─' * 40}")
            print(f"  Balance:    ${p['balance']:.2f}")
            print(f"  P&L:        {status['pnl_pct']:+.2f}%")
            print(f"  Positions:  {p['open_positions']}")

        print(f"\n{'=' * 60}\n")

    def print_positions(self):
        """Print current positions."""
        if not self._session:
            print("\n  No session loaded")
            return

        positions = self._session.portfolio.open_positions

        print(f"\n{'=' * 70}")
        print(f"{'OPEN POSITIONS':^70}")
        print(f"{'=' * 70}")

        if not positions:
            print(f"\n  No open positions\n")
            return

        print(
            f"\n  {'Symbol':<12} {'Side':<6} {'Entry':<12} {'Current':<12} {'P&L %':<10} {'P&L $':<10}"
        )
        print(f"  {'-' * 62}")

        prices = self._get_current_prices()

        for pos in positions:
            current_price = prices.get(pos.symbol, pos.entry_price)

            if pos.side == "LONG":
                pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            else:
                pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100

            pnl_usd = (pnl_pct / 100) * pos.size_usd

            pnl_color = "\033[92m" if pnl_pct >= 0 else "\033[91m"
            reset = "\033[0m"

            print(
                f"  {pos.symbol:<12} {pos.side:<6} "
                f"${pos.entry_price:<11.6f} ${current_price:<11.6f} "
                f"{pnl_color}{pnl_pct:>+8.2f}%{reset}  "
                f"{pnl_color}${pnl_usd:>+8.2f}{reset}"
            )

        print(f"\n{'=' * 70}\n")

    # =========================================================================
    # PROPERTIES
    # =========================================================================

    @property
    def session(self) -> Optional[PaperSession]:
        """Get current session."""
        return self._session

    @property
    def portfolio(self) -> Optional[PaperPortfolio]:
        """Get current portfolio."""
        return self._session.portfolio if self._session else None

    @property
    def open_positions(self) -> List[Position]:
        """Get open positions."""
        return self._session.portfolio.open_positions if self._session else []

    @property
    def balance(self) -> float:
        """Get current balance."""
        return self._session.portfolio.total_balance if self._session else 0.0


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def create_paper_engine(config_name: Optional[str] = None) -> PaperEngine:
    """Create paper engine instance."""
    return PaperEngine(config_name)
