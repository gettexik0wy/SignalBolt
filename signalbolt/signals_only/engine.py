"""
Signals-only engine.

Scans market for signals without executing trades.
Sends alerts via Telegram/Discord.

Usage:
    engine = SignalsOnlyEngine("config_safe.yaml")
    engine.start_session("my_signals")
    engine.run()
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable
from enum import Enum

from signalbolt.core.config import Config
from signalbolt.core.strategy import Signal
from signalbolt.signals_only.session import SignalsSession, StoredSignal
from signalbolt.signals_only.formatter import SignalFormatter
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.signals_only.engine")


class SignalsEngineState(Enum):
    """Engine state."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class SignalsOnlyEngine:
    """
    Signals-only engine.

    Scans market and generates alerts without trading.

    Usage:
        engine = SignalsOnlyEngine("config_safe.yaml")
        engine.start_session("alerts")
        engine.run()
    """

    def __init__(self, config_name: Optional[str] = None):
        """Initialize engine."""
        self.config_name = config_name or "config_safe.yaml"
        self.config: Optional[Config] = None

        # State
        self._state = SignalsEngineState.IDLE
        self._state_lock = threading.Lock()

        # Session
        self._session: Optional[SignalsSession] = None

        # Components
        self._exchange = None
        self._strategy = None
        self._data_manager = None
        self._price_feed = None
        self._scanner = None
        self._formatter = SignalFormatter()

        # Alert handlers
        self._telegram_handler = None
        self._discord_handler = None

        # Threading
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        # Stats
        self._scan_count = 0
        self._signal_count = 0
        self._alert_count = 0
        self._start_time: Optional[datetime] = None

        # Callbacks
        self._on_signal_callbacks: List[Callable[[Signal], None]] = []

        log.info("SignalsOnlyEngine initialized")

    # =========================================================================
    # STATE
    # =========================================================================

    @property
    def state(self) -> SignalsEngineState:
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: SignalsEngineState):
        with self._state_lock:
            old = self._state
            self._state = new_state
            log.info(f"Engine state: {old.value} -> {new_state.value}")

    # =========================================================================
    # SESSION
    # =========================================================================

    def start_session(self, session_name: str, create_new: bool = True) -> bool:
        """Start signals session."""
        try:
            # Load config
            Config.reset()
            self.config = Config(mode="signals", config_name=self.config_name)

            if create_new:
                self._session = SignalsSession.create(
                    name=session_name, config=self.config
                )
            else:
                self._session = SignalsSession.load(session_name)

            # Initialize components
            self._initialize_components()

            # Initialize alert handlers
            self._initialize_alerts()

            return True

        except Exception as e:
            log.error(f"Failed to start session: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _initialize_components(self):
        """Initialize scanning components."""
        try:
            # Exchange
            from signalbolt.exchange.client import get_exchange

            self._exchange = get_exchange(
                exchange_name=self.config.EXCHANGE_NAME,
                testnet=self.config.get("exchange", "testnet", default=False),
            )

            # Strategy
            from signalbolt.strategies.SignalBolt_original import SignalBoltOriginal

            self._strategy = SignalBoltOriginal(self.config)

            # Data manager
            from signalbolt.data.manager import DataManager

            self._data_manager = DataManager(
                mode="live", exchange=self._exchange, verbose=False
            )

            # Price feed
            from signalbolt.data.price_feed import PriceFeed

            self._price_feed = PriceFeed(self._exchange)

            # Scanner
            from signalbolt.signals.scanner import SignalScanner

            self._scanner = SignalScanner(
                exchange=self._exchange,
                config=self.config,
                strategy=self._strategy,
                data_manager=self._data_manager,
                price_feed=self._price_feed,
            )

            log.info("Components initialized")

        except Exception as e:
            log.error(f"Failed to initialize components: {e}")

    def _initialize_alerts(self):
        """Initialize alert handlers."""
        # Telegram
        if self.config.get("alerts", "telegram_enabled", default=False):
            try:
                from signalbolt.alerts.telegram import TelegramAlert

                self._telegram_handler = TelegramAlert(self.config)
                log.info("Telegram alerts enabled")
            except Exception as e:
                log.warning(f"Failed to initialize Telegram: {e}")

        # Discord
        if self.config.get("alerts", "discord_enabled", default=False):
            try:
                from signalbolt.alerts.discord import DiscordAlert

                self._discord_handler = DiscordAlert(self.config)
                log.info("Discord alerts enabled")
            except Exception as e:
                log.warning(f"Failed to initialize Discord: {e}")

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    def run(self):
        """Run engine (blocking)."""
        if not self._session:
            raise RuntimeError("No session. Call start_session() first.")

        self._start()

        try:
            self._main_loop()
        except KeyboardInterrupt:
            log.info("Interrupted")
        finally:
            self._stop()

    def _start(self):
        """Start engine."""
        self._set_state(SignalsEngineState.RUNNING)
        self._stop_event.clear()
        self._pause_event.set()
        self._start_time = datetime.now()
        self._scan_count = 0
        self._signal_count = 0
        self._alert_count = 0

        log.info(f"Engine started: {self._session.session_id}")

    def _main_loop(self):
        """Main scanning loop."""
        scan_interval = self.config.get("scanner", "scan_interval_sec", default=45)

        log.info(f"Starting scan loop (interval: {scan_interval}s)")

        while not self._stop_event.is_set():
            try:
                self._pause_event.wait()

                if self._stop_event.is_set():
                    break

                cycle_start = datetime.now()

                # Run scan
                if self._scanner:
                    try:
                        scan_result = self._scanner.scan_now()
                        self._scan_count += 1
                        self._session.update_scan_count()

                        # Process signals
                        for signal in scan_result.valid_signals:
                            self._handle_signal(signal)

                    except Exception as e:
                        log.error(f"Scan error: {e}")

                # Print status
                self._print_status()

                # Wait for next cycle
                elapsed = (datetime.now() - cycle_start).total_seconds()
                wait_time = max(0, scan_interval - elapsed)
                self._interruptible_sleep(wait_time)

            except Exception as e:
                log.error(f"Loop error: {e}")
                self._interruptible_sleep(5)

        log.info("Scan loop ended")

    def _handle_signal(self, signal: Signal):
        """Handle new signal."""
        self._signal_count += 1

        # Store signal
        stored = self._session.add_signal(signal)

        # Print to console
        print(self._formatter.console(signal))

        # Send alerts
        self._send_alerts(signal)

        # Fire callbacks
        for callback in self._on_signal_callbacks:
            try:
                callback(signal)
            except Exception as e:
                log.error(f"Callback error: {e}")

    def _send_alerts(self, signal: Signal):
        """Send signal alerts."""
        # Telegram
        if self._telegram_handler:
            try:
                msg = self._formatter.telegram_simple(signal)
                self._telegram_handler.send(msg)
                self._alert_count += 1
            except Exception as e:
                log.error(f"Telegram error: {e}")

        # Discord
        if self._discord_handler:
            try:
                payload = self._formatter.discord_webhook_payload(signal)
                self._discord_handler.send(payload)
                self._alert_count += 1
            except Exception as e:
                log.error(f"Discord error: {e}")

    def _print_status(self):
        """Print status line."""
        print(
            f"\r  [Scan #{self._scan_count}] "
            f"Signals: {self._signal_count} | "
            f"Alerts: {self._alert_count}   ",
            end="",
            flush=True,
        )

    def _stop(self):
        """Stop engine."""
        print()  # New line
        self._set_state(SignalsEngineState.STOPPED)
        log.info("Engine stopped")

    def _interruptible_sleep(self, seconds: float):
        """Sleep that can be interrupted."""
        interval = 0.5
        elapsed = 0.0
        while elapsed < seconds and not self._stop_event.is_set():
            time.sleep(min(interval, seconds - elapsed))
            elapsed += interval

    # =========================================================================
    # CONTROL
    # =========================================================================

    def stop(self):
        """Stop engine."""
        self._stop_event.set()
        self._pause_event.set()

    def pause(self):
        """Pause engine."""
        if self.state == SignalsEngineState.RUNNING:
            self._pause_event.clear()
            self._set_state(SignalsEngineState.PAUSED)

    def resume(self):
        """Resume engine."""
        if self.state == SignalsEngineState.PAUSED:
            self._pause_event.set()
            self._set_state(SignalsEngineState.RUNNING)

    # =========================================================================
    # CALLBACKS
    # =========================================================================

    def on_signal(self, callback: Callable[[Signal], None]):
        """Register signal callback."""
        self._on_signal_callbacks.append(callback)

    # =========================================================================
    # STATUS
    # =========================================================================

    def get_status(self) -> dict:
        """Get engine status."""
        runtime = 0.0
        if self._start_time:
            runtime = (datetime.now() - self._start_time).total_seconds() / 60

        return {
            "state": self.state.value,
            "session": self._session.name if self._session else None,
            "runtime_minutes": round(runtime, 1),
            "scan_count": self._scan_count,
            "signal_count": self._signal_count,
            "alert_count": self._alert_count,
            "telegram_enabled": self._telegram_handler is not None,
            "discord_enabled": self._discord_handler is not None,
        }

    def print_status(self):
        """Print formatted status."""
        status = self.get_status()

        print(f"\n{'=' * 50}")
        print(f"{'SIGNALS ENGINE STATUS':^50}")
        print(f"{'=' * 50}")
        print(f"\n  State:     {status['state'].upper()}")
        print(f"  Session:   {status['session'] or 'None'}")
        print(f"  Runtime:   {status['runtime_minutes']:.1f} min")
        print(f"  Scans:     {status['scan_count']}")
        print(f"  Signals:   {status['signal_count']}")
        print(f"  Alerts:    {status['alert_count']}")
        print(f"\n  Telegram:  {'✅ ON' if status['telegram_enabled'] else '❌ OFF'}")
        print(f"  Discord:   {'✅ ON' if status['discord_enabled'] else '❌ OFF'}")
        print(f"\n{'=' * 50}\n")

    # =========================================================================
    # PROPERTIES
    # =========================================================================

    @property
    def session(self) -> Optional[SignalsSession]:
        return self._session
