"""
Input validation utilities.

Validators for configuration, symbols, prices, etc.
"""

from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass
from pathlib import Path
import re

from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.utils.validators')


# =============================================================================
# VALIDATION RESULT
# =============================================================================

@dataclass
class ValidationResult:
    """Result of validation."""
    
    valid: bool
    errors: List[str]
    warnings: List[str]
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
    
    def __bool__(self) -> bool:
        return self.valid


def ok() -> ValidationResult:
    """Return valid result."""
    return ValidationResult(valid=True, errors=[], warnings=[])


def error(message: str) -> ValidationResult:
    """Return invalid result with error."""
    return ValidationResult(valid=False, errors=[message], warnings=[])


def warning(message: str) -> ValidationResult:
    """Return valid result with warning."""
    return ValidationResult(valid=True, errors=[], warnings=[message])


# =============================================================================
# SYMBOL VALIDATION
# =============================================================================

# Valid quote currencies
VALID_QUOTES = ['USDT', 'BUSD', 'BTC', 'ETH', 'BNB', 'USDC']

# Symbols to avoid (stablecoins, leveraged tokens, etc.)
SYMBOL_BLACKLIST = [
    'USDT', 'BUSD', 'USDC', 'DAI', 'TUSD', 'UST',  # Stablecoins
    'UP', 'DOWN', 'BULL', 'BEAR',  # Leveraged tokens (patterns)
]


def validate_symbol(symbol: str) -> ValidationResult:
    """
    Validate trading symbol.
    
    Rules:
    - Not empty
    - Alphanumeric only
    - Ends with valid quote currency
    - Not a stablecoin
    - Not a leveraged token
    """
    
    if not symbol:
        return error("Symbol cannot be empty")
    
    symbol = symbol.upper().strip()
    
    # Alphanumeric check
    if not symbol.isalnum():
        return error(f"Symbol must be alphanumeric: {symbol}")
    
    # Quote currency check
    has_valid_quote = any(symbol.endswith(q) for q in VALID_QUOTES)
    if not has_valid_quote:
        return error(f"Symbol must end with: {', '.join(VALID_QUOTES)}")
    
    # Blacklist check
    for pattern in SYMBOL_BLACKLIST:
        if pattern in symbol:
            return error(f"Symbol blacklisted (contains '{pattern}'): {symbol}")
    
    return ok()


def validate_symbols(symbols: List[str]) -> ValidationResult:
    """Validate list of symbols."""
    
    errors = []
    warnings = []
    
    for symbol in symbols:
        result = validate_symbol(symbol)
        errors.extend(result.errors)
        warnings.extend(result.warnings)
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


# =============================================================================
# PRICE VALIDATION
# =============================================================================

def validate_price(price: Any, name: str = "price") -> ValidationResult:
    """
    Validate price value.
    
    Rules:
    - Must be numeric
    - Must be positive
    - Must be finite
    """
    
    if price is None:
        return error(f"{name} cannot be None")
    
    if not isinstance(price, (int, float)):
        return error(f"{name} must be numeric, got {type(price).__name__}")
    
    if price <= 0:
        return error(f"{name} must be positive, got {price}")
    
    if not (price == price):  # NaN check
        return error(f"{name} is NaN")
    
    if price == float('inf') or price == float('-inf'):
        return error(f"{name} is infinite")
    
    return ok()


def validate_quantity(quantity: Any, name: str = "quantity") -> ValidationResult:
    """
    Validate quantity value.
    
    Same rules as price.
    """
    return validate_price(quantity, name)


# =============================================================================
# PERCENTAGE VALIDATION
# =============================================================================

def validate_percentage(
    value: Any,
    name: str = "value",
    min_val: float = 0,
    max_val: float = 100,
    allow_zero: bool = True
) -> ValidationResult:
    """
    Validate percentage value.
    
    Args:
        value: Value to validate
        name: Name for error messages
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        allow_zero: Whether zero is allowed
    """
    
    if value is None:
        return error(f"{name} cannot be None")
    
    if not isinstance(value, (int, float)):
        return error(f"{name} must be numeric, got {type(value).__name__}")
    
    if not allow_zero and value == 0:
        return error(f"{name} cannot be zero")
    
    if value < min_val:
        return error(f"{name} must be >= {min_val}, got {value}")
    
    if value > max_val:
        return error(f"{name} must be <= {max_val}, got {value}")
    
    return ok()


# =============================================================================
# CONFIG VALIDATION
# =============================================================================

def validate_config(config: Dict[str, Any]) -> ValidationResult:
    """
    Validate configuration dictionary.
    
    Checks all required sections and values.
    """
    
    errors = []
    warnings = []
    
    # Required sections
    required_sections = ['scanner', 'risk', 'strategy']
    
    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required section: {section}")
    
    # Scanner validation
    if 'scanner' in config:
        scanner = config['scanner']
        
        # min_signal_score
        if 'min_signal_score' in scanner:
            score = scanner['min_signal_score']
            result = validate_percentage(score, 'min_signal_score', 0, 120)
            errors.extend(result.errors)
            
            if score < 50:
                warnings.append(f"min_signal_score={score} is very low, may generate poor signals")
            elif score > 90:
                warnings.append(f"min_signal_score={score} is very high, may miss opportunities")
        
        # interval_seconds
        if 'interval_seconds' in scanner:
            interval = scanner['interval_seconds']
            if interval < 60:
                warnings.append(f"Scan interval {interval}s is very short, may cause rate limits")
    
    # Risk validation
    if 'risk' in config:
        risk = config['risk']
        
        # stop_loss_pct
        if 'stop_loss_pct' in risk:
            sl = risk['stop_loss_pct']
            result = validate_percentage(sl, 'stop_loss_pct', 0.1, 10)
            errors.extend(result.errors)
            
            if sl < 0.5:
                warnings.append(f"stop_loss_pct={sl}% is very tight, may cause frequent stops")
            elif sl > 5:
                warnings.append(f"stop_loss_pct={sl}% is very wide, high risk per trade")
        
        # max_positions
        if 'max_positions' in risk:
            max_pos = risk['max_positions']
            if not isinstance(max_pos, int) or max_pos < 1:
                errors.append("max_positions must be a positive integer")
            elif max_pos > 10:
                warnings.append(f"max_positions={max_pos} is high, may spread capital too thin")
    
    # Strategy validation
    if 'strategy' in config:
        strategy = config['strategy']
        
        # min_adx
        if 'min_adx' in strategy:
            adx = strategy['min_adx']
            result = validate_percentage(adx, 'min_adx', 0, 100)
            errors.extend(result.errors)
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


# =============================================================================
# FILE VALIDATION
# =============================================================================

def validate_file_exists(path: Union[str, Path], name: str = "file") -> ValidationResult:
    """Check if file exists."""
    
    path = Path(path)
    
    if not path.exists():
        return error(f"{name} not found: {path}")
    
    if not path.is_file():
        return error(f"{name} is not a file: {path}")
    
    return ok()


def validate_dir_exists(path: Union[str, Path], name: str = "directory") -> ValidationResult:
    """Check if directory exists."""
    
    path = Path(path)
    
    if not path.exists():
        return error(f"{name} not found: {path}")
    
    if not path.is_dir():
        return error(f"{name} is not a directory: {path}")
    
    return ok()


# =============================================================================
# API KEY VALIDATION
# =============================================================================

def validate_api_key(key: Optional[str], name: str = "API key") -> ValidationResult:
    """
    Validate API key format.
    
    Rules:
    - Not empty
    - Minimum length
    - Alphanumeric (Binance keys)
    """
    
    if not key:
        return error(f"{name} is empty")
    
    if len(key) < 20:
        return error(f"{name} is too short (minimum 20 characters)")
    
    # Binance keys are alphanumeric
    if not re.match(r'^[a-zA-Z0-9]+$', key):
        warnings = [f"{name} contains non-alphanumeric characters"]
        return ValidationResult(valid=True, errors=[], warnings=warnings)
    
    return ok()


# =============================================================================
# COMPOSITE VALIDATION
# =============================================================================

class Validator:
    """
    Composite validator.
    
    Usage:
        result = (Validator()
            .check(validate_symbol, symbol)
            .check(validate_price, price)
            .result())
    """
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def check(self, validator_fn, *args, **kwargs) -> 'Validator':
        """Run a validator and collect results."""
        
        result = validator_fn(*args, **kwargs)
        self.errors.extend(result.errors)
        self.warnings.extend(result.warnings)
        
        return self
    
    def require(self, condition: bool, error_msg: str) -> 'Validator':
        """Add error if condition is False."""
        
        if not condition:
            self.errors.append(error_msg)
        
        return self
    
    def warn_if(self, condition: bool, warning_msg: str) -> 'Validator':
        """Add warning if condition is True."""
        
        if condition:
            self.warnings.append(warning_msg)
        
        return self
    
    def result(self) -> ValidationResult:
        """Get final validation result."""
        
        return ValidationResult(
            valid=len(self.errors) == 0,
            errors=self.errors.copy(),
            warnings=self.warnings.copy()
        )
    
    @property
    def valid(self) -> bool:
        """Check if valid so far."""
        return len(self.errors) == 0