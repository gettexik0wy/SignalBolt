"""
Paper trading executor.

Executes signals as virtual trades:
- Opens positions with slippage simulation
- Monitors exits (SL, trailing, timeout)
- Tracks P&L
- Handles fees simulation
-  Captures indicator snapshots at entry/exit
"""

from typing import Optional, List, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from signalbolt.core.config import Config
from signalbolt.core.strategy import Signal, EntryPlan, ExitPlan, Strategy
from signalbolt.core.indicators import IndicatorValues, IndicatorCalculator
from signalbolt.paper.portfolio import PaperPortfolio, Position, TradeResult
from signalbolt.regime.detector import MarketRegime
from signalbolt.regime.presets import get_regime_preset
from signalbolt.data.price_feed import PriceFeed
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.paper.executor")


class ExitReason(Enum):
    """Reason for position exit."""

    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TAKE_PROFIT = "take_profit"
    TIMEOUT = "timeout"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ExecutionResult:
    """Result of signal execution."""

    success: bool
    position: Optional[Position] = None
    signal: Optional[Signal] = None
    error: Optional[str] = None

    # Execution details
    requested_price: float = 0.0
    executed_price: float = 0.0
    slippage_pct: float = 0.0
    fees_usd: float = 0.0

    def __str__(self) -> str:
        if self.success:
            return (
                f"✅ Executed {self.signal.symbol} @ {self.executed_price:.8f} "
                f"(slip: {self.slippage_pct:.3f}%)"
            )
        return f"❌ Failed: {self.error}"


@dataclass
class ExitCheckResult:
    """Result of exit condition check."""

    should_exit: bool
    reason: Optional[ExitReason] = None
    exit_price: float = 0.0
    details: str = ""


class PaperExecutor:
    """
    Executes paper trades.

    Features:
    - Signal → Position conversion
    - Slippage simulation (regime-based)
    - Fee simulation
    - Exit monitoring (SL, trailing, timeout)
    - Callbacks for trade events
    -  Indicator snapshot capture

    Usage:
        executor = PaperExecutor(portfolio, config)

        # Execute signal
        result = executor.execute_signal(signal, strategy)

        # Check exits (call periodically)
        closed = executor.check_exits()
    """

    def __init__(
        self,
        portfolio: PaperPortfolio,
        config: Config,
        price_feed: Optional[PriceFeed] = None,
        slippage_enabled: bool = True,
    ):
        """
        Initialize executor.

        Args:
            portfolio: Paper portfolio instance
            config: Configuration
            price_feed: Price feed (optional, creates default)
            slippage_enabled: Enable slippage simulation
        """
        self.portfolio = portfolio
        self.config = config
        self.price_feed = price_feed
        self.slippage_enabled = slippage_enabled

        # Fee settings
        self.fee_pct = config.get("exchange", "taker_fee_pct", default=0.1)

        #  Indicator calculator for exit snapshots
        self.indicator_calculator = IndicatorCalculator()

        #  Data manager for fetching candles at exit
        self.data_manager = None  # Will be lazy-loaded if needed

        # Callbacks
        self._on_entry_callbacks: List[Callable] = []
        self._on_exit_callbacks: List[Callable] = []

        # Stats
        self.total_executions = 0
        self.successful_executions = 0
        self.total_slippage_pct = 0.0

        log.info(
            f"PaperExecutor initialized (fee: {self.fee_pct}%, slippage: {'ON' if slippage_enabled else 'OFF'})"
        )

    # =========================================================================
    # SIGNAL EXECUTION
    # =========================================================================

    def execute_signal(
        self, signal: Signal, current_price: Optional[float] = None
    ) -> ExecutionResult:
        """
        Execute signal without strategy (simplified for scanner).

        Args:
            signal: Signal to execute
            current_price: Override current price

        Returns:
            ExecutionResult
        """

        self.total_executions += 1

        try:
            # 1. Get current price
            if current_price is None:
                ticker = (
                    self.price_feed.get_ticker(signal.symbol)
                    if self.price_feed
                    else None
                )
                if ticker:
                    current_price = ticker.last_price
                else:
                    current_price = signal.price

            # 2. Get available balance
            wallet_balance = self.portfolio.get_available_balance()

            if wallet_balance <= 0:
                return ExecutionResult(
                    success=False, signal=signal, error="No available balance"
                )

            # 3. Calculate position size (simple % of balance)
            wallet_pct = self.config.get("spot", "wallet_pct", default=50)
            position_size_usd = wallet_balance * (wallet_pct / 100)

            # 4. Calculate stop-loss
            sl_pct = abs(self.config.get("spot", "hard_sl_pct", default=-2.0))

            if signal.direction == "LONG":
                sl_price = current_price * (1 - sl_pct / 100)
            else:
                sl_price = current_price * (1 + sl_pct / 100)

            # 5. Calculate quantity
            quantity = position_size_usd / current_price

            # 6. Apply slippage
            regime = (
                MarketRegime(signal.regime)
                if signal.regime != "unknown"
                else MarketRegime.RANGE
            )
            slippage_pct = self._calculate_slippage(regime)

            if signal.direction == "LONG":
                executed_price = current_price * (1 + slippage_pct / 100)
            else:
                executed_price = current_price * (1 - slippage_pct / 100)

            # 7. Calculate fees
            fees_usd = position_size_usd * (self.fee_pct / 100)

            # 8. Recalculate with executed price
            actual_quantity = position_size_usd / executed_price

            if signal.direction == "LONG":
                actual_sl = executed_price * (1 - sl_pct / 100)
            else:
                actual_sl = executed_price * (1 + sl_pct / 100)

            # 9. Get trailing settings
            be_activation = self.config.get("spot", "be_activation_pct", default=0.5)
            trail_distance = self.config.get("spot", "trail_distance_pct", default=0.4)
            timeout_min = self.config.get("spot", "timeout_minutes", default=60)

            # 10. Open position
            position = self.portfolio.open_position(
                symbol=signal.symbol,
                direction=signal.direction,
                entry_price=executed_price,
                quantity=actual_quantity,
                size_usd=position_size_usd,
                stop_loss=actual_sl,
                stop_loss_pct=sl_pct,
                trailing_activation_pct=be_activation,
                trailing_distance_pct=trail_distance,
                timeout_minutes=timeout_min,
                signal_score=signal.score,
                fees_paid=fees_usd,
            )

            if position is None:
                return ExecutionResult(
                    success=False,
                    signal=signal,
                    error="Failed to open position in portfolio",
                )

            #  Attach entry indicators from signal
            if hasattr(signal, "indicators") and signal.indicators:
                position.entry_indicators = signal.indicators
                log.debug(f"Attached entry indicators to position {position.id}")
            else:
                log.debug(f"No indicators in signal for {signal.symbol}")

            # 11. Update stats
            self.successful_executions += 1
            self.total_slippage_pct += slippage_pct

            # 12. Fire callbacks
            for callback in self._on_entry_callbacks:
                try:
                    callback(position)
                except Exception as e:
                    log.error(f"Entry callback error: {e}")

            # 13. Log
            log.trade(
                f"📈 OPENED {signal.symbol} {signal.direction} | "
                f"Price: {executed_price:.8f} (slip: {slippage_pct:.3f}%) | "
                f"Size: ${position_size_usd:.2f} | "
                f"SL: {actual_sl:.8f} ({sl_pct:.2f}%) | "
                f"Score: {signal.score:.1f}"
            )

            return ExecutionResult(
                success=True,
                position=position,
                signal=signal,
                requested_price=current_price,
                executed_price=executed_price,
                slippage_pct=slippage_pct,
                fees_usd=fees_usd,
            )

        except Exception as e:
            log.error(f"Execution error for {signal.symbol}: {e}")
            import traceback

            log.debug(traceback.format_exc())
            return ExecutionResult(success=False, signal=signal, error=str(e))

    # =========================================================================
    # EXIT MONITORING
    # =========================================================================

    def check_exits(self) -> List[TradeResult]:
        """
        Check all open positions for exit conditions.

        Checks:
        1. Stop-loss hit
        2. Trailing stop triggered
        3. Timeout reached

        Returns:
            List of closed positions (TradeResult)
        """

        results = []
        positions = self.portfolio.get_open_positions()

        for position in positions:
            check = self._check_position_exit(position)

            if check.should_exit:
                result = self._close_position(position, check)
                if result:
                    results.append(result)

        return results

    def _check_position_exit(self, position: Position) -> ExitCheckResult:
        """
        Check if position should exit.

        Args:
            position: Position to check

        Returns:
            ExitCheckResult
        """

        # Get current price
        current_price = self.price_feed.get_price(position.symbol)

        if current_price is None:
            log.warning(f"Cannot get price for {position.symbol}")
            return ExitCheckResult(should_exit=False)

        # Update trailing stop
        self.portfolio.update_trailing_stop(position.id, current_price)

        # Reload position (trailing might have updated)
        position = self.portfolio.get_position(position.id)

        # 1. Check stop-loss
        if self._is_stop_loss_hit(position, current_price):
            return ExitCheckResult(
                should_exit=True,
                reason=ExitReason.TRAILING_STOP
                if position.trailing_activated
                else ExitReason.STOP_LOSS,
                exit_price=current_price,
                details=f"SL hit at {current_price:.8f} (SL: {position.current_stop_loss:.8f})",
            )

        # 2. Check timeout
        if self._is_timeout_reached(position):
            # Check if profitable enough to close
            pnl_pct = position.get_pnl_pct(current_price)
            min_profit = 0.15  # Min profit to close on timeout

            if pnl_pct >= min_profit:
                return ExitCheckResult(
                    should_exit=True,
                    reason=ExitReason.TIMEOUT,
                    exit_price=current_price,
                    details=f"Timeout with profit ({pnl_pct:.2f}%)",
                )
            else:
                # Extend timeout or close at loss?
                # For now, close anyway
                return ExitCheckResult(
                    should_exit=True,
                    reason=ExitReason.TIMEOUT,
                    exit_price=current_price,
                    details=f"Timeout ({pnl_pct:.2f}%)",
                )

        return ExitCheckResult(should_exit=False)

    def _is_stop_loss_hit(self, position: Position, current_price: float) -> bool:
        """Check if stop-loss is hit."""

        if position.direction == "LONG":
            return current_price <= position.current_stop_loss
        else:  # SHORT
            return current_price >= position.current_stop_loss

    def _is_timeout_reached(self, position: Position) -> bool:
        """Check if position timeout is reached."""

        if position.timeout_at is None:
            return False

        return datetime.now() >= position.timeout_at

    def _close_position(
        self, position: Position, exit_check: ExitCheckResult
    ) -> Optional[TradeResult]:
        """
        Close position.

        Args:
            position: Position to close
            exit_check: Exit check result

        Returns:
            TradeResult or None on error
        """

        try:
            # Apply exit slippage
            regime = MarketRegime.RANGE  # Default, could detect from BTC
            slippage_pct = self._calculate_slippage(regime)

            if position.direction == "LONG":
                exit_price = exit_check.exit_price * (1 - slippage_pct / 100)
            else:
                exit_price = exit_check.exit_price * (1 + slippage_pct / 100)

            # Calculate exit fees
            position_value = position.quantity * exit_price
            exit_fees = position_value * (self.fee_pct / 100)

            #  Try to capture exit indicators BEFORE closing
            exit_indicators = None
            try:
                # Lazy-load data manager if needed
                if self.data_manager is None:
                    from signalbolt.data.manager import DataManager

                    self.data_manager = DataManager(mode="live", exchange=None)

                # Fetch recent candles (default 5m timeframe)
                df = self.data_manager.get_candles(
                    symbol=position.symbol, interval="5m", limit=100
                )

                if df is not None and len(df) >= 50:
                    df_ind = self.indicator_calculator.calculate(df, position.symbol)
                    exit_indicators = self.indicator_calculator.get_latest(df_ind)
                    log.debug(f"Captured exit indicators for {position.symbol}")
                else:
                    log.debug(
                        f"Insufficient data for exit indicators: {position.symbol}"
                    )

            except Exception as e:
                log.debug(
                    f"Could not capture exit indicators for {position.symbol}: {e}"
                )

            # Close in portfolio
            result = self.portfolio.close_position(
                position_id=position.id,
                exit_price=exit_price,
                exit_reason=exit_check.reason.value,
                exit_fees=exit_fees,
            )

            if result is None:
                return None

            #  Attach exit indicators to the result's position
            if exit_indicators and hasattr(result, "position") and result.position:
                result.position.exit_indicators = exit_indicators
                log.debug(f"Attached exit indicators to closed position {position.id}")

            # Fire callbacks
            for callback in self._on_exit_callbacks:
                try:
                    callback(result)
                except Exception as e:
                    log.error(f"Exit callback error: {e}")

            # Log
            emoji = "🟢" if result.pnl_pct > 0 else "🔴"
            log.trade(
                f"{emoji} CLOSED {position.symbol} | "
                f"Exit: {exit_price:.8f} | "
                f"P&L: {result.pnl_usd:+.2f} USD ({result.pnl_pct:+.2f}%) | "
                f"Reason: {exit_check.reason.value} | "
                f"Duration: {result.duration_minutes:.0f}min"
            )

            return result

        except Exception as e:
            log.error(f"Close position error: {e}")
            return None

    # =========================================================================
    # MANUAL OPERATIONS
    # =========================================================================

    def close_position_manual(
        self, position_id: str, reason: str = "manual"
    ) -> Optional[TradeResult]:
        """
        Manually close a position.

        Args:
            position_id: Position ID to close
            reason: Close reason

        Returns:
            TradeResult or None
        """

        position = self.portfolio.get_position(position_id)
        if position is None:
            log.warning(f"Position not found: {position_id}")
            return None

        current_price = self.price_feed.get_price(position.symbol)
        if current_price is None:
            log.error(f"Cannot get price for {position.symbol}")
            return None

        exit_check = ExitCheckResult(
            should_exit=True,
            reason=ExitReason.MANUAL,
            exit_price=current_price,
            details=reason,
        )

        return self._close_position(position, exit_check)

    def close_all_positions(self, reason: str = "close_all") -> List[TradeResult]:
        """
        Close all open positions.

        Args:
            reason: Close reason

        Returns:
            List of TradeResult
        """

        results = []
        positions = self.portfolio.get_open_positions()

        for position in positions:
            result = self.close_position_manual(position.id, reason)
            if result:
                results.append(result)

        log.info(f"Closed {len(results)} positions ({reason})")

        return results

    def emergency_close_all(self) -> List[TradeResult]:
        """Emergency close all positions."""
        log.warning("🚨 EMERGENCY CLOSE ALL POSITIONS")
        return self.close_all_positions("emergency")

    # =========================================================================
    # SLIPPAGE
    # =========================================================================

    def _calculate_slippage(self, regime: MarketRegime) -> float:
        """
        Calculate slippage percentage based on regime.

        Args:
            regime: Current market regime

        Returns:
            Slippage percentage
        """
        if not self.slippage_enabled:
            return 0.0  # No slippage

        preset = get_regime_preset(regime, self.config)
        base_slippage = preset.slippage_pct

        # Add small random variation (±20%)
        import random

        variation = random.uniform(0.8, 1.2)

        return base_slippage * variation

    # =========================================================================
    # CALLBACKS
    # =========================================================================

    def on_entry(self, callback: Callable[[Position], None]):
        """Register entry callback."""
        self._on_entry_callbacks.append(callback)

    def on_exit(self, callback: Callable[[TradeResult], None]):
        """Register exit callback."""
        self._on_exit_callbacks.append(callback)

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> dict:
        """Get executor statistics."""

        return {
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "success_rate": (
                self.successful_executions / self.total_executions * 100
                if self.total_executions > 0
                else 0
            ),
            "avg_slippage_pct": (
                self.total_slippage_pct / self.successful_executions
                if self.successful_executions > 0
                else 0
            ),
            "fee_pct": self.fee_pct,
        }

    def reset_stats(self):
        """Reset executor statistics."""
        self.total_executions = 0
        self.successful_executions = 0
        self.total_slippage_pct = 0.0

    def __repr__(self) -> str:
        return (
            f"PaperExecutor(executions={self.total_executions}, "
            f"success={self.successful_executions})"
        )
