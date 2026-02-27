"""
Offline replay engine.

Simulates what happened during offline period:
1. Downloads historical data for the offline window
2. Replays signals/trades as if bot was running
3. Updates portfolio with results
4. Provides detailed replay report

This is the KILLER FEATURE of SignalBolt!

Usage:
    replay = ReplayEngine(session, strategy, config)

    # Check if replay needed
    if session.needs_replay():
        start, end = session.get_replay_period()

        # Run replay
        result = replay.run(start, end)

        # Show what happened
        print(result.summary())

        # Mark as complete
        session.mark_replay_complete()
"""

import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field

import pandas as pd

from signalbolt.core.config import Config
from signalbolt.core.strategy import Strategy, Signal
from signalbolt.core.indicators import IndicatorCalculator
from signalbolt.core.filters import SignalFilter
from signalbolt.data.manager import DataManager
from signalbolt.paper.portfolio import CloseReason
from signalbolt.paper.session import PaperSession
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.paper.replay")


# =============================================================================
# REPLAY RESULT
# =============================================================================


@dataclass
class ReplayedTrade:
    """Trade that occurred during replay."""

    symbol: str
    direction: str

    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float

    quantity: float
    size_usd: float

    pnl_pct: float
    pnl_usd: float
    net_pnl_usd: float

    close_reason: str
    signal_score: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat(),
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "size_usd": round(self.size_usd, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "pnl_usd": round(self.pnl_usd, 2),
            "net_pnl_usd": round(self.net_pnl_usd, 2),
            "close_reason": self.close_reason,
            "signal_score": self.signal_score,
        }


@dataclass
class ReplayResult:
    """Result of offline replay."""

    # Period
    replay_start: datetime
    replay_end: datetime
    duration_hours: float

    # Simulation stats
    candles_processed: int
    scans_simulated: int
    signals_generated: int
    signals_executed: int

    # Trades
    trades: List[ReplayedTrade]
    trades_opened: int
    trades_closed: int

    # P&L
    starting_balance: float
    ending_balance: float
    total_pnl_usd: float
    total_pnl_pct: float

    # Position state
    positions_still_open: int
    open_position_symbols: List[str]

    # Timing
    replay_time_seconds: float

    # Errors
    errors: List[str] = field(default_factory=list)

    @property
    def has_activity(self) -> bool:
        """Check if anything happened during offline period."""
        return self.signals_generated > 0 or self.trades_opened > 0

    def summary(self) -> str:
        """Get human-readable summary."""
        lines = [
            "📊 OFFLINE REPLAY SUMMARY",
            "─" * 40,
            f"Period: {self.replay_start.strftime('%Y-%m-%d %H:%M')} → {self.replay_end.strftime('%Y-%m-%d %H:%M')}",
            f"Duration: {self.duration_hours:.1f} hours",
            "",
            "📈 Activity:",
            f"  Scans simulated: {self.scans_simulated}",
            f"  Signals found: {self.signals_generated}",
            f"  Trades executed: {self.signals_executed}",
            f"  Trades closed: {self.trades_closed}",
            "",
        ]

        if self.trades:
            lines.append("💰 P&L:")
            lines.append(f"  Starting: ${self.starting_balance:.2f}")
            lines.append(f"  Ending: ${self.ending_balance:.2f}")

            pnl_emoji = "🟢" if self.total_pnl_usd >= 0 else "🔴"
            lines.append(
                f"  Net P&L: {pnl_emoji} ${self.total_pnl_usd:+.2f} ({self.total_pnl_pct:+.2f}%)"
            )
            lines.append("")

            lines.append("📋 Trades:")
            for trade in self.trades:
                emoji = "🟢" if trade.net_pnl_usd >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {trade.symbol}: {trade.net_pnl_usd:+.2f} ({trade.close_reason})"
                )
        else:
            lines.append("No trades during offline period.")

        if self.positions_still_open > 0:
            lines.append("")
            lines.append(
                f"⚠️ {self.positions_still_open} position(s) still open: {', '.join(self.open_position_symbols)}"
            )

        lines.append("")
        lines.append(f"Replay completed in {self.replay_time_seconds:.1f}s")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "replay_start": self.replay_start.isoformat(),
            "replay_end": self.replay_end.isoformat(),
            "duration_hours": round(self.duration_hours, 2),
            "candles_processed": self.candles_processed,
            "scans_simulated": self.scans_simulated,
            "signals_generated": self.signals_generated,
            "signals_executed": self.signals_executed,
            "trades_opened": self.trades_opened,
            "trades_closed": self.trades_closed,
            "starting_balance": round(self.starting_balance, 2),
            "ending_balance": round(self.ending_balance, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "positions_still_open": self.positions_still_open,
            "open_position_symbols": self.open_position_symbols,
            "trades": [t.to_dict() for t in self.trades],
            "replay_time_seconds": round(self.replay_time_seconds, 2),
            "errors": self.errors,
        }


# =============================================================================
# REPLAY POSITION (internal tracking)
# =============================================================================


@dataclass
class ReplayPosition:
    """Position during replay."""

    symbol: str
    direction: str

    entry_time: datetime
    entry_price: float
    quantity: float
    size_usd: float

    # Risk params
    stop_loss_price: float
    stop_loss_pct: float
    trailing_activation_pct: float
    trailing_distance_pct: float
    timeout_minutes: int

    # State
    trailing_active: bool = False
    highest_price: float = 0.0
    highest_pnl_pct: float = 0.0
    current_price: float = 0.0

    # Signal info
    signal_score: float = 0.0

    def update_price(self, price: float):
        """Update with new price."""
        self.current_price = price

        if price > self.highest_price:
            self.highest_price = price

        pnl = self.current_pnl_pct
        if pnl > self.highest_pnl_pct:
            self.highest_pnl_pct = pnl

    @property
    def current_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0

        if self.direction == "LONG":
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.current_price) / self.entry_price) * 100

    def check_stop_loss(self, price: float) -> bool:
        """Check if SL triggered."""
        if self.direction == "LONG":
            return price <= self.stop_loss_price
        else:
            return price >= self.stop_loss_price

    def update_trailing(self) -> bool:
        """Update trailing stop. Returns True if changed."""
        pnl = self.current_pnl_pct

        # Activate trailing
        if not self.trailing_active and pnl >= self.trailing_activation_pct:
            self.trailing_active = True

            # Move SL to breakeven
            if self.direction == "LONG":
                self.stop_loss_price = self.entry_price * 1.001
            else:
                self.stop_loss_price = self.entry_price * 0.999

            return True

        # Update trailing
        if self.trailing_active:
            trail_distance = self.trailing_distance_pct / 100

            if self.direction == "LONG":
                new_sl = self.highest_price * (1 - trail_distance)
                if new_sl > self.stop_loss_price:
                    self.stop_loss_price = new_sl
                    return True
            else:
                new_sl = self.highest_price * (1 + trail_distance)
                if new_sl < self.stop_loss_price:
                    self.stop_loss_price = new_sl
                    return True

        return False


# =============================================================================
# REPLAY ENGINE
# =============================================================================


class ReplayEngine:
    """
    Replay engine for offline paper trading.

    Simulates what would have happened during an offline period:
    1. Downloads historical OHLCV data
    2. Replays scans at configured interval
    3. Generates signals using actual strategy
    4. Executes trades and manages positions
    5. Returns complete replay report
    """

    def __init__(
        self,
        session: PaperSession,
        strategy: Strategy,
        config: Config,
        data_manager: Optional[DataManager] = None,
    ):
        """
        Initialize replay engine.

        Args:
            session: Paper session to replay into
            strategy: Trading strategy
            config: SignalBolt config
            data_manager: Data manager (creates if None)
        """
        self.session = session
        self.strategy = strategy
        self.config = config

        # Components
        self.data_manager = data_manager or DataManager(mode="backtest", verbose=False)
        self.indicator_calc = IndicatorCalculator()
        self.filter = SignalFilter(config)

        # Settings
        self.scan_interval_sec = config.get("scanner", "scan_interval_sec", default=45)
        self.scan_interval_candles = max(1, self.scan_interval_sec // 300)  # 5m candles
        self.min_signal_score = config.get("scanner", "min_signal_score", default=70)
        self.signal_cooldown_min = config.get(
            "scanner", "signal_cooldown_min", default=30
        )

        # Position settings
        self.wallet_pct = config.get("spot", "wallet_pct", default=50)
        self.hard_sl_pct = config.get("spot", "hard_sl_pct", default=-2.0)
        self.be_activation_pct = config.get("spot", "be_activation_pct", default=0.5)
        self.trail_distance_pct = config.get("spot", "trail_distance_pct", default=0.4)
        self.timeout_minutes = config.get("spot", "timeout_minutes", default=60)
        self.taker_fee_pct = config.get("exchange", "taker_fee_pct", default=0.04)

        # Callbacks
        self._on_progress: Optional[Callable[[float, str], None]] = None

        log.info("ReplayEngine initialized")

    # =========================================================================
    # MAIN REPLAY
    # =========================================================================

    def run(
        self,
        start_time: datetime,
        end_time: datetime,
        symbols: Optional[List[str]] = None,
        verbose: bool = True,
    ) -> ReplayResult:
        """
        Run offline replay.

        Args:
            start_time: Start of offline period
            end_time: End of offline period
            symbols: Symbols to scan (uses session watched symbols if None)
            verbose: Print progress

        Returns:
            ReplayResult
        """
        replay_start = time.time()

        if verbose:
            log.info("=" * 60)
            log.info("OFFLINE REPLAY")
            log.info(f"Period: {start_time} → {end_time}")
            log.info("=" * 60)

        # Get symbols
        symbols = symbols or self.session.get_watched_symbols()

        if not symbols:
            # Default: use some top coins
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
            log.warning(f"No watched symbols, using defaults: {symbols}")

        # Initialize result
        result = ReplayResult(
            replay_start=start_time,
            replay_end=end_time,
            duration_hours=(end_time - start_time).total_seconds() / 3600,
            candles_processed=0,
            scans_simulated=0,
            signals_generated=0,
            signals_executed=0,
            trades=[],
            trades_opened=0,
            trades_closed=0,
            starting_balance=self.session.portfolio.total_balance,
            ending_balance=0.0,
            total_pnl_usd=0.0,
            total_pnl_pct=0.0,
            positions_still_open=0,
            open_position_symbols=[],
            replay_time_seconds=0.0,
        )

        try:
            # Download historical data
            if verbose:
                log.info(f"Downloading data for {len(symbols)} symbols...")

            symbol_data = self._download_data(symbols, start_time, end_time)

            if not symbol_data:
                result.errors.append("No data available for replay period")
                return self._finalize_result(result, replay_start)

            if verbose:
                log.info(f"Downloaded data for {len(symbol_data)} symbols")

            # Run simulation
            result = self._simulate(symbol_data, start_time, end_time, result, verbose)

        except Exception as e:
            log.error(f"Replay error: {e}")
            result.errors.append(str(e))

        return self._finalize_result(result, replay_start)

    def _finalize_result(self, result: ReplayResult, start_time: float) -> ReplayResult:
        """Finalize replay result."""
        result.replay_time_seconds = time.time() - start_time
        result.ending_balance = self.session.portfolio.total_balance
        result.total_pnl_usd = result.ending_balance - result.starting_balance

        if result.starting_balance > 0:
            result.total_pnl_pct = (
                result.total_pnl_usd / result.starting_balance
            ) * 100

        result.positions_still_open = self.session.portfolio.open_position_count
        result.open_position_symbols = self.session.portfolio.open_symbols

        return result

    # =========================================================================
    # DATA DOWNLOAD
    # =========================================================================

    def _download_data(
        self, symbols: List[str], start_time: datetime, end_time: datetime
    ) -> Dict[str, pd.DataFrame]:
        """Download historical data for all symbols."""

        symbol_data: Dict[str, pd.DataFrame] = {}

        # Add buffer for indicators
        buffer_start = start_time - timedelta(days=1)

        for symbol in symbols:
            try:
                df = self.data_manager.get_historical(
                    symbol=symbol,
                    interval="5m",
                    start_date=buffer_start.strftime("%Y-%m-%d"),
                    end_date=end_time.strftime("%Y-%m-%d"),
                    market_type="spot",
                )

                if df is not None and len(df) > 100:
                    symbol_data[symbol] = df
                else:
                    log.warning(f"Insufficient data for {symbol}")

            except Exception as e:
                log.warning(f"Failed to download {symbol}: {e}")

        return symbol_data

    # =========================================================================
    # SIMULATION
    # =========================================================================

    def _simulate(
        self,
        symbol_data: Dict[str, pd.DataFrame],
        start_time: datetime,
        end_time: datetime,
        result: ReplayResult,
        verbose: bool,
    ) -> ReplayResult:
        """Run simulation over the data."""

        # State
        virtual_balance = self.session.portfolio.total_balance
        open_positions: Dict[str, ReplayPosition] = {}
        last_signal_time: Dict[str, datetime] = {}
        completed_trades: List[ReplayedTrade] = []

        # Get existing open positions from session
        for position in self.session.portfolio.open_positions:
            open_positions[position.symbol] = ReplayPosition(
                symbol=position.symbol,
                direction=position.direction,
                entry_time=position.entry_time,
                entry_price=position.entry_price,
                quantity=position.quantity,
                size_usd=position.size_usd,
                stop_loss_price=position.stop_loss_price,
                stop_loss_pct=position.stop_loss_pct,
                trailing_activation_pct=position.trailing_activation_pct,
                trailing_distance_pct=position.trailing_distance_pct,
                timeout_minutes=self.timeout_minutes,
                trailing_active=position.trailing_active,
                highest_price=position.highest_price,
                highest_pnl_pct=position.highest_pnl_pct,
                signal_score=position.signal_score,
            )

        # Get unified timeline (all candle timestamps)
        all_timestamps = set()
        for df in symbol_data.values():
            all_timestamps.update(df.index.tolist())

        # Filter to replay period
        all_timestamps = sorted(
            [ts for ts in all_timestamps if start_time <= ts <= end_time]
        )

        if not all_timestamps:
            log.warning("No timestamps in replay period")
            return result

        total_steps = len(all_timestamps)

        if verbose:
            log.info(f"Simulating {total_steps} time steps...")

        # Main simulation loop
        for step_idx, current_time in enumerate(all_timestamps):
            result.candles_processed += 1

            # Progress callback
            if self._on_progress and step_idx % 100 == 0:
                progress = step_idx / total_steps
                self._on_progress(progress, f"Step {step_idx}/{total_steps}")

            # Get current prices
            current_prices: Dict[str, float] = {}
            for symbol, df in symbol_data.items():
                if current_time in df.index:
                    current_prices[symbol] = float(df.loc[current_time, "close"])

            # === UPDATE OPEN POSITIONS ===
            positions_to_close = []

            for symbol, position in open_positions.items():
                if symbol not in current_prices:
                    continue

                price = current_prices[symbol]
                position.update_price(price)
                position.update_trailing()

                # Calculate hold time
                hold_minutes = (current_time - position.entry_time).total_seconds() / 60

                # Check exit conditions
                close_reason = None

                # 1. Stop loss
                if position.check_stop_loss(price):
                    if position.trailing_active:
                        close_reason = CloseReason.TRAILING_SL
                    else:
                        close_reason = CloseReason.HARD_SL

                # 2. Timeout
                elif hold_minutes >= position.timeout_minutes:
                    if position.current_pnl_pct >= 0.15:
                        close_reason = CloseReason.TIMEOUT
                    elif (
                        position.highest_pnl_pct > 0.3
                        and position.current_pnl_pct < 0.1
                    ):
                        close_reason = CloseReason.TIMEOUT
                    elif hold_minutes >= position.timeout_minutes + 30:
                        close_reason = CloseReason.TIMEOUT

                if close_reason:
                    positions_to_close.append(
                        (symbol, price, close_reason, current_time)
                    )

            # Close positions
            for symbol, exit_price, reason, exit_time in positions_to_close:
                position = open_positions.pop(symbol)

                # Calculate P&L
                if position.direction == "LONG":
                    pnl_pct = (
                        (exit_price - position.entry_price) / position.entry_price
                    ) * 100
                else:
                    pnl_pct = (
                        (position.entry_price - exit_price) / position.entry_price
                    ) * 100

                pnl_usd = position.size_usd * (pnl_pct / 100)
                fees = (
                    position.size_usd * (self.taker_fee_pct / 100) * 2
                )  # Entry + exit
                net_pnl = pnl_usd - fees

                # Update balance
                virtual_balance += position.size_usd + net_pnl

                # Record trade
                trade = ReplayedTrade(
                    symbol=symbol,
                    direction=position.direction,
                    entry_time=position.entry_time,
                    entry_price=position.entry_price,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    quantity=position.quantity,
                    size_usd=position.size_usd,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                    net_pnl_usd=net_pnl,
                    close_reason=reason.value,
                    signal_score=position.signal_score,
                )

                completed_trades.append(trade)
                result.trades_closed += 1

                if verbose:
                    emoji = "🟢" if net_pnl > 0 else "🔴"
                    log.info(
                        f"  {emoji} CLOSE {symbol}: {net_pnl:+.2f} ({reason.value})"
                    )

            # === SCAN FOR SIGNALS ===
            # Only scan at intervals
            if step_idx % self.scan_interval_candles != 0:
                continue

            result.scans_simulated += 1

            # Check each symbol for signals
            for symbol in symbol_data.keys():
                # Skip if already have position
                if symbol in open_positions:
                    continue

                # Skip if in cooldown
                if symbol in last_signal_time:
                    elapsed = (
                        current_time - last_signal_time[symbol]
                    ).total_seconds() / 60
                    if elapsed < self.signal_cooldown_min:
                        continue

                # Skip if max positions reached
                if len(open_positions) >= self.config.get(
                    "spot", "max_positions", default=1
                ):
                    continue

                # Get data up to current point
                df = symbol_data[symbol]
                mask = df.index <= current_time
                df_current = df[mask].tail(150)

                if len(df_current) < 100:
                    continue

                # Generate signal
                signal = self._generate_signal(df_current, symbol, current_time)

                if signal:
                    result.signals_generated += 1
                    last_signal_time[symbol] = current_time

                    # Execute if score OK
                    if signal.score >= self.min_signal_score:
                        # Calculate position size
                        size_usd = virtual_balance * (self.wallet_pct / 100)

                        if size_usd >= 10:
                            entry_price = signal.price
                            quantity = size_usd / entry_price

                            # Calculate SL
                            if signal.direction == "LONG":
                                sl_price = entry_price * (1 + self.hard_sl_pct / 100)
                            else:
                                sl_price = entry_price * (1 - self.hard_sl_pct / 100)

                            # Deduct from balance
                            entry_fee = size_usd * (self.taker_fee_pct / 100)
                            virtual_balance -= size_usd + entry_fee

                            # Create position
                            position = ReplayPosition(
                                symbol=symbol,
                                direction=signal.direction,
                                entry_time=current_time,
                                entry_price=entry_price,
                                quantity=quantity,
                                size_usd=size_usd,
                                stop_loss_price=sl_price,
                                stop_loss_pct=self.hard_sl_pct,
                                trailing_activation_pct=self.be_activation_pct,
                                trailing_distance_pct=self.trail_distance_pct,
                                timeout_minutes=self.timeout_minutes,
                                highest_price=entry_price,
                                signal_score=signal.score,
                            )

                            open_positions[symbol] = position
                            result.signals_executed += 1
                            result.trades_opened += 1

                            if verbose:
                                log.info(
                                    f"  📈 OPEN {signal.direction} {symbol} @ {entry_price:.8f} "
                                    f"(score: {signal.score:.1f})"
                                )

        # === FINALIZE ===

        # Update session portfolio with replay results
        self._apply_to_portfolio(open_positions, completed_trades, virtual_balance)

        result.trades = completed_trades

        if verbose:
            log.info(
                f"Simulation complete: {result.trades_closed} trades closed, "
                f"{len(open_positions)} still open"
            )

        return result

    # =========================================================================
    # SIGNAL GENERATION
    # =========================================================================

    def _generate_signal(
        self, df: pd.DataFrame, symbol: str, current_time: datetime
    ) -> Optional[Signal]:
        """Generate signal using strategy."""
        try:
            # Calculate indicators
            df_with_ind = self.indicator_calc.calculate(df.copy(), symbol)
            indicators = self.indicator_calc.get_latest(df_with_ind)

            # Use strategy
            signal = self.strategy.generate_signal(
                df_with_ind, symbol, indicators.close
            )

            if signal:
                signal.timestamp = current_time

            return signal

        except Exception as e:
            log.debug(f"Signal generation error for {symbol}: {e}")
            return None

    # =========================================================================
    # APPLY TO PORTFOLIO
    # =========================================================================

    def _apply_to_portfolio(
        self,
        open_positions: Dict[str, ReplayPosition],
        completed_trades: List[ReplayedTrade],
        final_balance: float,
    ):
        """Apply replay results to session portfolio."""
        portfolio = self.session.portfolio

        # Close all current positions in portfolio
        for position in list(portfolio.open_positions):
            # Find matching replay position
            if position.symbol in open_positions:
                replay_pos = open_positions[position.symbol]

                # Update position state
                position.current_price = replay_pos.current_price
                position.highest_price = replay_pos.highest_price
                position.highest_pnl_pct = replay_pos.highest_pnl_pct
                position.stop_loss_price = replay_pos.stop_loss_price
                position.trailing_active = replay_pos.trailing_active

            # If not in open_positions, it was closed during replay
            else:
                # Find the matching completed trade
                for trade in completed_trades:
                    if trade.symbol == position.symbol:
                        # Close in portfolio
                        portfolio.close_position(
                            position_id=position.id,
                            exit_price=trade.exit_price,
                            reason=CloseReason(trade.close_reason),
                        )
                        break

        # Add new positions opened during replay
        for symbol, replay_pos in open_positions.items():
            if not portfolio.has_position(symbol):
                # Open new position in portfolio
                portfolio.open_position(
                    symbol=symbol,
                    direction=replay_pos.direction,
                    quantity=replay_pos.quantity,
                    entry_price=replay_pos.entry_price,
                    stop_loss_pct=replay_pos.stop_loss_pct,
                    trailing_activation_pct=replay_pos.trailing_activation_pct,
                    trailing_distance_pct=replay_pos.trailing_distance_pct,
                    signal_score=replay_pos.signal_score,
                    signal_regime="replay",
                    notes="Opened during offline replay",
                )

        # Update portfolio balance
        log.info(f"Portfolio updated: balance ${portfolio.total_balance:.2f}")

    # =========================================================================
    # CALLBACKS
    # =========================================================================

    def on_progress(self, callback: Callable[[float, str], None]):
        """
        Set progress callback.

        Args:
            callback: Function(progress_pct, message)
        """
        self._on_progress = callback


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def replay_offline_period(
    session: PaperSession, strategy: Strategy, config: Config, verbose: bool = True
) -> Optional[ReplayResult]:
    """
    Replay offline period for session.

    Args:
        session: Paper session
        strategy: Trading strategy
        config: Config
        verbose: Print progress

    Returns:
        ReplayResult or None if no replay needed
    """
    if not session.needs_replay():
        log.info("No replay needed")
        return None

    start, end = session.get_replay_period()

    engine = ReplayEngine(session, strategy, config)
    result = engine.run(start, end, verbose=verbose)

    if not result.errors:
        session.mark_replay_complete()

    return result
