"""
Regime-specific configuration presets.

Each market regime (BULL, BEAR, RANGE, CRASH) has optimized parameters:
- Stop-loss %
- Breakeven trigger %
- Trailing stop %
- Min signal score
- Slippage assumptions
- Position sizing

Based on backtest_production.py results.
"""

from dataclasses import dataclass
from typing import Dict

from signalbolt.core.config import Config
from signalbolt.regime.detector import MarketRegime
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.regime.presets")


@dataclass
class RegimePreset:
    """Configuration preset for a market regime."""

    # Risk parameters
    stop_loss_pct: float
    breakeven_trigger_pct: float
    trailing_stop_pct: float

    # Signal quality
    min_signal_score: float

    # Execution
    slippage_pct: float

    # Position sizing
    wallet_pct: float
    max_positions: int

    # Behavior
    scan_interval_sec: int

    def to_dict(self) -> dict:
        return {
            "stop_loss_pct": self.stop_loss_pct,
            "breakeven_trigger_pct": self.breakeven_trigger_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
            "min_signal_score": self.min_signal_score,
            "slippage_pct": self.slippage_pct,
            "wallet_pct": self.wallet_pct,
            "max_positions": self.max_positions,
            "scan_interval_sec": self.scan_interval_sec,
        }


# =============================================================================
# PRESET DEFINITIONS
# =============================================================================

BULL_PRESET = RegimePreset(
    stop_loss_pct=1.2,  # Tighter SL (momentum is strong)
    breakeven_trigger_pct=0.6,  # Quick breakeven
    trailing_stop_pct=0.5,  # Tight trail (lock profits)
    min_signal_score=65.0,  # Lower threshold (more opportunities)
    slippage_pct=0.05,  # Low slippage (high liquidity)
    wallet_pct=80.0,  # Aggressive sizing
    max_positions=5,  # More positions (favorable conditions)
    scan_interval_sec=300,  # 5 min (active scanning)
)

RANGE_PRESET = RegimePreset(
    stop_loss_pct=1.5,  # Medium SL
    breakeven_trigger_pct=0.8,  # Medium breakeven
    trailing_stop_pct=0.6,  # Medium trail
    min_signal_score=70.0,  # Balanced threshold
    slippage_pct=0.08,  # Medium slippage
    wallet_pct=70.0,  # Balanced sizing
    max_positions=3,  # Moderate positions
    scan_interval_sec=600,  # 10 min
)

BEAR_PRESET = RegimePreset(
    stop_loss_pct=2.0,  # Wider SL (volatile)
    breakeven_trigger_pct=1.0,  # Conservative breakeven
    trailing_stop_pct=0.8,  # Wider trail (avoid early exit)
    min_signal_score=75.0,  # Higher threshold (be selective)
    slippage_pct=0.12,  # Higher slippage (lower liquidity)
    wallet_pct=50.0,  # Conservative sizing
    max_positions=2,  # Fewer positions (risk management)
    scan_interval_sec=900,  # 15 min (less frequent)
)

CRASH_PRESET = RegimePreset(
    stop_loss_pct=2.5,  # Wide SL (extreme volatility)
    breakeven_trigger_pct=1.2,  # Very conservative
    trailing_stop_pct=1.0,  # Wide trail
    min_signal_score=80.0,  # Very high threshold (only best signals)
    slippage_pct=0.20,  # High slippage (panic mode)
    wallet_pct=30.0,  # Very conservative sizing
    max_positions=1,  # Single position only
    scan_interval_sec=1800,  # 30 min (wait for stabilization)
)

# Default preset (fallback)
DEFAULT_PRESET = RANGE_PRESET


# =============================================================================
# PRESET REGISTRY
# =============================================================================

PRESETS: Dict[MarketRegime, RegimePreset] = {
    MarketRegime.BULL: BULL_PRESET,
    MarketRegime.RANGE: RANGE_PRESET,
    MarketRegime.BEAR: BEAR_PRESET,
    MarketRegime.CRASH: CRASH_PRESET,
}


# =============================================================================
# PUBLIC API
# =============================================================================


def get_regime_preset(regime: MarketRegime, config: Config) -> RegimePreset:
    """
    Get preset for regime (with config overrides).

    Args:
        regime: Market regime
        config: Config instance (for overrides)

    Returns:
        RegimePreset instance

    Usage:
        preset = get_regime_preset(MarketRegime.BULL, config)
        print(preset.stop_loss_pct)  # 1.2
    """

    base_preset = PRESETS.get(regime, DEFAULT_PRESET)

    # Check for config overrides
    overrides = config.get("regime_overrides", regime.value, default={})

    if overrides:
        log.debug(f"Applying overrides for {regime.value}: {overrides}")

        return RegimePreset(
            stop_loss_pct=overrides.get("stop_loss_pct", base_preset.stop_loss_pct),
            breakeven_trigger_pct=overrides.get(
                "breakeven_trigger_pct", base_preset.breakeven_trigger_pct
            ),
            trailing_stop_pct=overrides.get(
                "trailing_stop_pct", base_preset.trailing_stop_pct
            ),
            min_signal_score=overrides.get(
                "min_signal_score", base_preset.min_signal_score
            ),
            slippage_pct=overrides.get("slippage_pct", base_preset.slippage_pct),
            wallet_pct=overrides.get("wallet_pct", base_preset.wallet_pct),
            max_positions=overrides.get("max_positions", base_preset.max_positions),
            scan_interval_sec=overrides.get(
                "scan_interval_sec", base_preset.scan_interval_sec
            ),
        )

    return base_preset


def get_all_presets() -> Dict[str, RegimePreset]:
    """Get all presets (for display/comparison)."""
    return {regime.value: preset for regime, preset in PRESETS.items()}


def print_presets():
    """Print all presets in table format with colors."""

    print("\n" + "=" * 95)
    print("🌍 REGIME PRESETS")
    print("=" * 95)

    # Emoji mapping
    emoji_map = {
        MarketRegime.BULL: "🟢",
        MarketRegime.RANGE: "🟡",
        MarketRegime.BEAR: "🔴",
        MarketRegime.CRASH: "⚠️",
    }

    headers = [
        "Regime",
        "SL%",
        "BE%",
        "Trail%",
        "MinScore",
        "Slip%",
        "Wallet%",
        "MaxPos",
        "ScanSec",
    ]
    print(
        f"   {headers[0]:<10} {headers[1]:>6} {headers[2]:>6} {headers[3]:>7} "
        f"{headers[4]:>9} {headers[5]:>6} {headers[6]:>8} {headers[7]:>7} {headers[8]:>8}"
    )
    print("-" * 95)

    for regime, preset in PRESETS.items():
        emoji = emoji_map.get(regime, "❓")
        print(
            f"{emoji} {regime.value:<10} "
            f"{preset.stop_loss_pct:>6.2f} "
            f"{preset.breakeven_trigger_pct:>6.2f} "
            f"{preset.trailing_stop_pct:>7.2f} "
            f"{preset.min_signal_score:>9.1f} "
            f"{preset.slippage_pct:>6.2f} "
            f"{preset.wallet_pct:>8.1f} "
            f"{preset.max_positions:>7} "
            f"{preset.scan_interval_sec:>8}"
        )

    print("=" * 95 + "\n")

    # Print notes
    print("📝 Notes:")
    print("  • SL%      = Stop-loss percentage")
    print("  • BE%      = Breakeven trigger percentage")
    print("  • Trail%   = Trailing stop distance")
    print("  • MinScore = Minimum signal score required")
    print("  • Slip%    = Expected slippage percentage")
    print("  • Wallet%  = Percentage of balance per trade")
    print("  • MaxPos   = Maximum concurrent positions")
    print("  • ScanSec  = Scan interval in seconds")
    print()


# =============================================================================
# CLI HELPER
# =============================================================================

if __name__ == "__main__":
    """Print presets when run directly."""
    print_presets()
