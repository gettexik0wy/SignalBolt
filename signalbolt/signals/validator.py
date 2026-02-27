"""
Pre-trade signal validator.

Final validation layer before trade execution:
- Balance checks
- Position limits
- Market conditions (spread, liquidity, volatility)
- Timing checks (stale signals, market hours)
- Sanity checks (price deviation, size limits)

Usage:
    validator = SignalValidator(exchange, config)
    
    # Full validation
    result = validator.validate(signal, wallet_balance=1000, open_positions=0)
    
    if result.is_valid:
        execute_trade(signal)
    else:
        log.warning(f"Rejected: {result.rejection_reason}")
    
    # Quick check
    if validator.can_trade(signal):
        execute_trade(signal)
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from enum import Enum

from signalbolt.core.config import Config
from signalbolt.core.strategy import Signal, EntryPlan
from signalbolt.core.risk import RiskManager, PositionSize
from signalbolt.exchange.base import ExchangeBase, Ticker
from signalbolt.data.price_feed import PriceFeed
from signalbolt.data.liquidity import LiquidityAnalyzer, LiquidityTier
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.signals.validator')


# =============================================================================
# VALIDATION TYPES
# =============================================================================

class ValidationStage(Enum):
    """Validation stage identifiers."""
    SIGNAL_FRESHNESS = "signal_freshness"
    BALANCE = "balance"
    POSITION_LIMIT = "position_limit"
    SPREAD = "spread"
    LIQUIDITY = "liquidity"
    VOLATILITY = "volatility"
    PRICE_DEVIATION = "price_deviation"
    SIZE_LIMIT = "size_limit"
    RISK_CHECK = "risk_check"
    SANITY = "sanity"


class ValidationSeverity(Enum):
    """Validation failure severity."""
    HARD_REJECT = "hard_reject"    # Cannot proceed
    SOFT_REJECT = "soft_reject"    # Can override
    WARNING = "warning"            # Proceed with caution


# =============================================================================
# VALIDATION RESULT
# =============================================================================

@dataclass
class ValidationCheck:
    """Single validation check result."""
    
    stage: ValidationStage
    passed: bool
    severity: ValidationSeverity
    message: str
    
    # Details
    actual_value: Optional[float] = None
    threshold: Optional[float] = None
    
    def to_dict(self) -> dict:
        return {
            'stage': self.stage.value,
            'passed': self.passed,
            'severity': self.severity.value,
            'message': self.message,
            'actual': self.actual_value,
            'threshold': self.threshold,
        }


@dataclass
class ValidationResult:
    """Complete validation result."""
    
    signal: Signal
    timestamp: datetime
    
    # Checks
    checks: List[ValidationCheck] = field(default_factory=list)
    
    # Overall result
    is_valid: bool = False
    can_override: bool = False  # True if only soft rejects
    
    # Rejection info
    rejection_reason: Optional[str] = None
    rejection_stage: Optional[ValidationStage] = None
    
    # Warnings
    warnings: List[str] = field(default_factory=list)
    
    # Adjusted values (if any)
    adjusted_size_usd: Optional[float] = None
    adjusted_entry_price: Optional[float] = None
    
    # Validation time
    validation_time_ms: float = 0.0
    
    @property
    def passed_checks(self) -> List[ValidationCheck]:
        """Get passed checks."""
        return [c for c in self.checks if c.passed]
    
    @property
    def failed_checks(self) -> List[ValidationCheck]:
        """Get failed checks."""
        return [c for c in self.checks if not c.passed]
    
    @property
    def hard_rejects(self) -> List[ValidationCheck]:
        """Get hard reject failures."""
        return [c for c in self.checks 
                if not c.passed and c.severity == ValidationSeverity.HARD_REJECT]
    
    @property
    def soft_rejects(self) -> List[ValidationCheck]:
        """Get soft reject failures."""
        return [c for c in self.checks 
                if not c.passed and c.severity == ValidationSeverity.SOFT_REJECT]
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.signal.symbol,
            'timestamp': self.timestamp.isoformat(),
            'is_valid': self.is_valid,
            'can_override': self.can_override,
            'rejection_reason': self.rejection_reason,
            'rejection_stage': self.rejection_stage.value if self.rejection_stage else None,
            'warnings': self.warnings,
            'checks': [c.to_dict() for c in self.checks],
            'validation_time_ms': round(self.validation_time_ms, 2),
        }
    
    def summary(self) -> str:
        """Get human-readable summary."""
        if self.is_valid:
            if self.warnings:
                return f"✓ Valid with {len(self.warnings)} warning(s)"
            return "✓ Valid"
        
        if self.can_override:
            return f"⚠ Soft reject: {self.rejection_reason}"
        
        return f"✗ Rejected: {self.rejection_reason}"


# =============================================================================
# VALIDATOR CONFIG
# =============================================================================

@dataclass
class ValidatorConfig:
    """Validator configuration."""
    
    # Signal freshness
    max_signal_age_sec: float = 30.0
    
    # Balance
    min_balance_usd: float = 10.0
    min_trade_size_usd: float = 10.0
    max_trade_size_pct: float = 95.0  # Max % of balance
    
    # Position limits
    max_positions: int = 1
    max_same_symbol_positions: int = 1
    
    # Spread
    max_spread_pct: float = 0.5
    warn_spread_pct: float = 0.3
    
    # Liquidity
    min_volume_24h: float = 5_000_000
    min_liquidity_tier: LiquidityTier = LiquidityTier.LOW
    
    # Volatility
    max_atr_pct: float = 10.0  # Reject if ATR > 10%
    warn_atr_pct: float = 5.0
    
    # Price deviation
    max_price_deviation_pct: float = 2.0  # Signal price vs current
    
    # Size limits
    min_quantity: float = 0.0  # Set per symbol
    max_quantity: float = float('inf')
    
    # Risk
    max_risk_per_trade_pct: float = 5.0
    max_portfolio_risk_pct: float = 15.0


# =============================================================================
# SIGNAL VALIDATOR
# =============================================================================

class SignalValidator:
    """
    Validate signals before trade execution.
    
    Performs comprehensive checks:
    1. Signal freshness
    2. Balance availability
    3. Position limits
    4. Market conditions (spread, liquidity, volatility)
    5. Price deviation
    6. Size limits
    7. Risk checks
    
    Usage:
        validator = SignalValidator(exchange, config)
        
        result = validator.validate(
            signal=signal,
            wallet_balance=1000,
            open_positions=0
        )
        
        if result.is_valid:
            # Execute trade
            pass
        elif result.can_override:
            # Ask user or use conservative size
            pass
        else:
            # Hard reject
            log.warning(result.summary())
    """
    
    def __init__(
        self,
        exchange: ExchangeBase,
        config: Config,
        validator_config: Optional[ValidatorConfig] = None,
        price_feed: Optional[PriceFeed] = None,
        liquidity_analyzer: Optional[LiquidityAnalyzer] = None,
        risk_manager: Optional[RiskManager] = None
    ):
        """
        Initialize validator.
        
        Args:
            exchange: Exchange instance
            config: SignalBolt config
            validator_config: Validation config
            price_feed: Price feed (creates if None)
            liquidity_analyzer: Liquidity analyzer (creates if None)
            risk_manager: Risk manager (creates if None)
        """
        self.exchange = exchange
        self.config = config
        self.val_config = validator_config or self._load_config(config)
        
        # Components
        self.price_feed = price_feed or PriceFeed(exchange)
        self.liquidity = liquidity_analyzer or LiquidityAnalyzer(exchange, self.price_feed)
        self.risk_manager = risk_manager or RiskManager(config)
        
        log.info("SignalValidator initialized")
    
    def _load_config(self, config: Config) -> ValidatorConfig:
        """Load config from SignalBolt config."""
        return ValidatorConfig(
            max_signal_age_sec=config.get('validator', 'max_signal_age_sec', default=30.0),
            min_balance_usd=config.get('validator', 'min_balance_usd', default=10.0),
            min_trade_size_usd=config.get('spot', 'min_trade_usd', default=10.0),
            max_positions=config.get('spot', 'max_positions', default=1),
            max_spread_pct=config.get('discovery', 'max_spread_pct', default=0.5),
            min_volume_24h=config.get('discovery', 'min_volume_24h', default=5_000_000),
            max_risk_per_trade_pct=config.get('risk', 'risk_pct_per_trade', default=5.0),
        )
    
    # =========================================================================
    # MAIN VALIDATION
    # =========================================================================
    
    def validate(
        self,
        signal: Signal,
        wallet_balance: float,
        open_positions: int = 0,
        current_position_symbols: Optional[List[str]] = None,
        entry_plan: Optional[EntryPlan] = None
    ) -> ValidationResult:
        """
        Perform full validation of signal.
        
        Args:
            signal: Signal to validate
            wallet_balance: Available wallet balance
            open_positions: Number of open positions
            current_position_symbols: Symbols of open positions
            entry_plan: Entry plan (if already calculated)
        
        Returns:
            ValidationResult
        """
        start_time = datetime.now()
        
        result = ValidationResult(
            signal=signal,
            timestamp=start_time
        )
        
        current_position_symbols = current_position_symbols or []
        
        # Get current market data
        ticker = self.price_feed.get_ticker(signal.symbol)
        
        if not ticker:
            result.checks.append(ValidationCheck(
                stage=ValidationStage.SANITY,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message="Failed to fetch current ticker"
            ))
            result.rejection_reason = "Cannot fetch market data"
            result.rejection_stage = ValidationStage.SANITY
            return self._finalize(result, start_time)
        
        # === RUN ALL CHECKS ===
        
        # 1. Signal freshness
        result.checks.append(self._check_signal_freshness(signal))
        
        # 2. Balance
        result.checks.append(self._check_balance(wallet_balance))
        
        # 3. Position limits
        result.checks.append(self._check_position_limit(
            open_positions, 
            signal.symbol, 
            current_position_symbols
        ))
        
        # 4. Spread
        result.checks.append(self._check_spread(ticker))
        
        # 5. Liquidity
        result.checks.append(self._check_liquidity(signal.symbol))
        
        # 6. Volatility
        if signal.indicators:
            result.checks.append(self._check_volatility(signal.indicators.atr_pct))
        
        # 7. Price deviation
        result.checks.append(self._check_price_deviation(signal.price, ticker.last))
        
        # 8. Size limits
        if entry_plan:
            result.checks.append(self._check_size_limits(
                entry_plan.position_size_usd,
                entry_plan.quantity,
                wallet_balance
            ))
        
        # 9. Risk check
        result.checks.append(self._check_risk(
            wallet_balance,
            open_positions
        ))
        
        # 10. Sanity checks
        result.checks.append(self._check_sanity(signal, ticker))
        
        # === DETERMINE RESULT ===
        
        hard_rejects = result.hard_rejects
        soft_rejects = result.soft_rejects
        
        if hard_rejects:
            # First hard reject is the reason
            first_reject = hard_rejects[0]
            result.is_valid = False
            result.can_override = False
            result.rejection_reason = first_reject.message
            result.rejection_stage = first_reject.stage
        
        elif soft_rejects:
            # Soft rejects can be overridden
            result.is_valid = False
            result.can_override = True
            result.rejection_reason = soft_rejects[0].message
            result.rejection_stage = soft_rejects[0].stage
        
        else:
            result.is_valid = True
        
        # Collect warnings
        for check in result.checks:
            if check.passed and check.severity == ValidationSeverity.WARNING:
                result.warnings.append(check.message)
        
        return self._finalize(result, start_time)
    
    def _finalize(
        self,
        result: ValidationResult,
        start_time: datetime
    ) -> ValidationResult:
        """Finalize result with timing."""
        result.validation_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        if result.is_valid:
            log.debug(f"Validation passed: {result.signal.symbol}")
        else:
            log.info(f"Validation failed: {result.signal.symbol} - {result.rejection_reason}")
        
        return result
    
    # =========================================================================
    # QUICK CHECKS
    # =========================================================================
    
    def can_trade(
        self,
        signal: Signal,
        wallet_balance: float = 0,
        open_positions: int = 0
    ) -> bool:
        """
        Quick check if signal can be traded.
        
        Args:
            signal: Signal to check
            wallet_balance: Wallet balance
            open_positions: Open positions
        
        Returns:
            True if can trade
        """
        result = self.validate(signal, wallet_balance, open_positions)
        return result.is_valid
    
    def quick_check(
        self,
        symbol: str,
        wallet_balance: float,
        open_positions: int = 0
    ) -> Tuple[bool, Optional[str]]:
        """
        Quick pre-check without full signal.
        
        Args:
            symbol: Trading symbol
            wallet_balance: Wallet balance
            open_positions: Open positions
        
        Returns:
            (can_trade, rejection_reason)
        """
        # Balance check
        if wallet_balance < self.val_config.min_balance_usd:
            return False, f"Insufficient balance: ${wallet_balance:.2f}"
        
        # Position limit
        if open_positions >= self.val_config.max_positions:
            return False, f"Position limit reached: {open_positions}/{self.val_config.max_positions}"
        
        # Spread check
        ticker = self.price_feed.get_ticker(symbol)
        if ticker and ticker.spread_pct > self.val_config.max_spread_pct:
            return False, f"Spread too wide: {ticker.spread_pct:.2f}%"
        
        # Liquidity check
        if not self.liquidity.is_tradeable(symbol):
            return False, "Insufficient liquidity"
        
        return True, None
    
    # =========================================================================
    # INDIVIDUAL CHECKS
    # =========================================================================
    
    def _check_signal_freshness(self, signal: Signal) -> ValidationCheck:
        """Check if signal is fresh enough."""
        age = (datetime.now() - signal.timestamp).total_seconds()
        max_age = self.val_config.max_signal_age_sec
        
        if age > max_age:
            return ValidationCheck(
                stage=ValidationStage.SIGNAL_FRESHNESS,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Signal too old: {age:.1f}s > {max_age}s",
                actual_value=age,
                threshold=max_age
            )
        
        if age > max_age * 0.7:
            return ValidationCheck(
                stage=ValidationStage.SIGNAL_FRESHNESS,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Signal aging: {age:.1f}s",
                actual_value=age,
                threshold=max_age
            )
        
        return ValidationCheck(
            stage=ValidationStage.SIGNAL_FRESHNESS,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Signal fresh: {age:.1f}s",
            actual_value=age,
            threshold=max_age
        )
    
    def _check_balance(self, wallet_balance: float) -> ValidationCheck:
        """Check wallet balance."""
        min_balance = self.val_config.min_balance_usd
        
        if wallet_balance < min_balance:
            return ValidationCheck(
                stage=ValidationStage.BALANCE,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Insufficient balance: ${wallet_balance:.2f} < ${min_balance:.2f}",
                actual_value=wallet_balance,
                threshold=min_balance
            )
        
        min_trade = self.val_config.min_trade_size_usd
        
        if wallet_balance < min_trade * 2:
            return ValidationCheck(
                stage=ValidationStage.BALANCE,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Low balance: ${wallet_balance:.2f}",
                actual_value=wallet_balance,
                threshold=min_trade * 2
            )
        
        return ValidationCheck(
            stage=ValidationStage.BALANCE,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Balance OK: ${wallet_balance:.2f}",
            actual_value=wallet_balance,
            threshold=min_balance
        )
    
    def _check_position_limit(
        self,
        open_positions: int,
        symbol: str,
        current_symbols: List[str]
    ) -> ValidationCheck:
        """Check position limits."""
        max_positions = self.val_config.max_positions
        
        if open_positions >= max_positions:
            return ValidationCheck(
                stage=ValidationStage.POSITION_LIMIT,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Max positions reached: {open_positions}/{max_positions}",
                actual_value=float(open_positions),
                threshold=float(max_positions)
            )
        
        # Check same symbol
        symbol_count = current_symbols.count(symbol)
        max_same = self.val_config.max_same_symbol_positions
        
        if symbol_count >= max_same:
            return ValidationCheck(
                stage=ValidationStage.POSITION_LIMIT,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Already have position in {symbol}",
                actual_value=float(symbol_count),
                threshold=float(max_same)
            )
        
        return ValidationCheck(
            stage=ValidationStage.POSITION_LIMIT,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Position slots: {open_positions}/{max_positions}",
            actual_value=float(open_positions),
            threshold=float(max_positions)
        )
    
    def _check_spread(self, ticker: Ticker) -> ValidationCheck:
        """Check bid-ask spread."""
        spread = ticker.spread_pct
        max_spread = self.val_config.max_spread_pct
        warn_spread = self.val_config.warn_spread_pct
        
        if spread > max_spread:
            return ValidationCheck(
                stage=ValidationStage.SPREAD,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Spread too wide: {spread:.3f}% > {max_spread}%",
                actual_value=spread,
                threshold=max_spread
            )
        
        if spread > warn_spread:
            return ValidationCheck(
                stage=ValidationStage.SPREAD,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Spread elevated: {spread:.3f}%",
                actual_value=spread,
                threshold=warn_spread
            )
        
        return ValidationCheck(
            stage=ValidationStage.SPREAD,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Spread OK: {spread:.3f}%",
            actual_value=spread,
            threshold=max_spread
        )
    
    def _check_liquidity(self, symbol: str) -> ValidationCheck:
        """Check liquidity."""
        liquidity_info = self.liquidity.analyze(symbol)
        
        if not liquidity_info.is_tradeable:
            return ValidationCheck(
                stage=ValidationStage.LIQUIDITY,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Insufficient liquidity: {liquidity_info.tier.value}",
                actual_value=liquidity_info.volume_24h,
                threshold=self.val_config.min_volume_24h
            )
        
        if liquidity_info.tier == LiquidityTier.LOW:
            return ValidationCheck(
                stage=ValidationStage.LIQUIDITY,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Low liquidity: ${liquidity_info.volume_24h:,.0f}",
                actual_value=liquidity_info.volume_24h,
                threshold=self.val_config.min_volume_24h
            )
        
        return ValidationCheck(
            stage=ValidationStage.LIQUIDITY,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Liquidity {liquidity_info.tier.value}: ${liquidity_info.volume_24h:,.0f}",
            actual_value=liquidity_info.volume_24h,
            threshold=self.val_config.min_volume_24h
        )
    
    def _check_volatility(self, atr_pct: float) -> ValidationCheck:
        """Check volatility (ATR)."""
        max_atr = self.val_config.max_atr_pct
        warn_atr = self.val_config.warn_atr_pct
        
        if atr_pct > max_atr:
            return ValidationCheck(
                stage=ValidationStage.VOLATILITY,
                passed=False,
                severity=ValidationSeverity.SOFT_REJECT,
                message=f"Extreme volatility: ATR {atr_pct:.2f}% > {max_atr}%",
                actual_value=atr_pct,
                threshold=max_atr
            )
        
        if atr_pct > warn_atr:
            return ValidationCheck(
                stage=ValidationStage.VOLATILITY,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"High volatility: ATR {atr_pct:.2f}%",
                actual_value=atr_pct,
                threshold=warn_atr
            )
        
        return ValidationCheck(
            stage=ValidationStage.VOLATILITY,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Volatility OK: ATR {atr_pct:.2f}%",
            actual_value=atr_pct,
            threshold=max_atr
        )
    
    def _check_price_deviation(
        self,
        signal_price: float,
        current_price: float
    ) -> ValidationCheck:
        """Check if current price deviated from signal price."""
        if signal_price == 0:
            return ValidationCheck(
                stage=ValidationStage.PRICE_DEVIATION,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message="No signal price to compare"
            )
        
        deviation = abs((current_price - signal_price) / signal_price) * 100
        max_deviation = self.val_config.max_price_deviation_pct
        
        if deviation > max_deviation:
            return ValidationCheck(
                stage=ValidationStage.PRICE_DEVIATION,
                passed=False,
                severity=ValidationSeverity.SOFT_REJECT,
                message=f"Price moved: {deviation:.2f}% from signal",
                actual_value=deviation,
                threshold=max_deviation
            )
        
        if deviation > max_deviation * 0.5:
            return ValidationCheck(
                stage=ValidationStage.PRICE_DEVIATION,
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Price drifting: {deviation:.2f}%",
                actual_value=deviation,
                threshold=max_deviation
            )
        
        return ValidationCheck(
            stage=ValidationStage.PRICE_DEVIATION,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Price stable: {deviation:.2f}%",
            actual_value=deviation,
            threshold=max_deviation
        )
    
    def _check_size_limits(
        self,
        size_usd: float,
        quantity: float,
        wallet_balance: float
    ) -> ValidationCheck:
        """Check trade size limits."""
        min_size = self.val_config.min_trade_size_usd
        max_pct = self.val_config.max_trade_size_pct
        max_size = wallet_balance * (max_pct / 100)
        
        if size_usd < min_size:
            return ValidationCheck(
                stage=ValidationStage.SIZE_LIMIT,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Trade too small: ${size_usd:.2f} < ${min_size:.2f}",
                actual_value=size_usd,
                threshold=min_size
            )
        
        if size_usd > max_size:
            return ValidationCheck(
                stage=ValidationStage.SIZE_LIMIT,
                passed=False,
                severity=ValidationSeverity.SOFT_REJECT,
                message=f"Trade too large: ${size_usd:.2f} > ${max_size:.2f} ({max_pct}%)",
                actual_value=size_usd,
                threshold=max_size
            )
        
        return ValidationCheck(
            stage=ValidationStage.SIZE_LIMIT,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message=f"Size OK: ${size_usd:.2f}",
            actual_value=size_usd,
            threshold=max_size
        )
    
    def _check_risk(
        self,
        wallet_balance: float,
        open_positions: int
    ) -> ValidationCheck:
        """Check risk limits."""
        # Use risk manager checks
        can_open, reason = self.risk_manager.can_open_position(
            current_positions=open_positions,
            total_risk_pct=0  # Would need to calculate
        )
        
        if not can_open:
            return ValidationCheck(
                stage=ValidationStage.RISK_CHECK,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Risk check failed: {reason}"
            )
        
        # Check emergency stop
        if self.risk_manager.emergency_stop_active:
            return ValidationCheck(
                stage=ValidationStage.RISK_CHECK,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message="Emergency stop active"
            )
        
        # Check max drawdown
        if self.risk_manager.max_drawdown_hit:
            return ValidationCheck(
                stage=ValidationStage.RISK_CHECK,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message="Max drawdown limit reached"
            )
        
        return ValidationCheck(
            stage=ValidationStage.RISK_CHECK,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message="Risk checks passed"
        )
    
    def _check_sanity(self, signal: Signal, ticker: Ticker) -> ValidationCheck:
        """Final sanity checks."""
        issues = []
        
        # Price sanity
        if ticker.last <= 0:
            issues.append("Invalid price")
        
        # Direction sanity
        if signal.direction not in ['LONG', 'SHORT']:
            issues.append(f"Invalid direction: {signal.direction}")
        
        # Score sanity
        if signal.score < 0 or signal.score > 200:
            issues.append(f"Invalid score: {signal.score}")
        
        # Symbol match
        if signal.symbol != ticker.symbol:
            issues.append("Symbol mismatch")
        
        if issues:
            return ValidationCheck(
                stage=ValidationStage.SANITY,
                passed=False,
                severity=ValidationSeverity.HARD_REJECT,
                message=f"Sanity check failed: {', '.join(issues)}"
            )
        
        return ValidationCheck(
            stage=ValidationStage.SANITY,
            passed=True,
            severity=ValidationSeverity.WARNING,
            message="Sanity checks passed"
        )
    
    # =========================================================================
    # CONFIGURATION
    # =========================================================================
    
    def set_max_spread(self, spread_pct: float):
        """Set maximum spread."""
        self.val_config.max_spread_pct = spread_pct
    
    def set_max_positions(self, max_positions: int):
        """Set maximum positions."""
        self.val_config.max_positions = max_positions
    
    def set_min_balance(self, min_balance: float):
        """Set minimum balance."""
        self.val_config.min_balance_usd = min_balance
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def get_config(self) -> dict:
        """Get current configuration."""
        return {
            'max_signal_age_sec': self.val_config.max_signal_age_sec,
            'min_balance_usd': self.val_config.min_balance_usd,
            'min_trade_size_usd': self.val_config.min_trade_size_usd,
            'max_positions': self.val_config.max_positions,
            'max_spread_pct': self.val_config.max_spread_pct,
            'min_volume_24h': self.val_config.min_volume_24h,
            'max_atr_pct': self.val_config.max_atr_pct,
            'max_price_deviation_pct': self.val_config.max_price_deviation_pct,
            'max_risk_per_trade_pct': self.val_config.max_risk_per_trade_pct,
        }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_validator(
    exchange: ExchangeBase,
    config: Config
) -> SignalValidator:
    """Create signal validator instance."""
    return SignalValidator(exchange, config)


def validate_signal(
    signal: Signal,
    exchange: ExchangeBase,
    config: Config,
    wallet_balance: float,
    open_positions: int = 0
) -> ValidationResult:
    """Quick validation function."""
    validator = SignalValidator(exchange, config)
    return validator.validate(signal, wallet_balance, open_positions)