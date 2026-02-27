"""
Paper trading statistics and reporting.

Features:
- Performance metrics (Sharpe, Sortino, Calmar, etc.)
- Trade analysis (win rate, avg P&L, etc.)
- Time-based breakdowns (daily, weekly, monthly)
- Regime-based analysis
- Export to CSV/JSON/HTML
- Charts data preparation

Usage:
    stats = PaperStats(portfolio)
    
    # Get summary
    summary = stats.get_summary()
    
    # Get detailed metrics
    metrics = stats.get_performance_metrics()
    
    # Export
    stats.export_csv("trades.csv")
    stats.export_json("stats.json")
    
    # Print formatted report
    print(stats.format_report())
"""

import math
import json
import csv
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from pathlib import Path

from signalbolt.paper.portfolio import PaperPortfolio, Position, TradeResult
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.paper.stats')


# =============================================================================
# CONSTANTS
# =============================================================================

# Risk-free rate for Sharpe calculation (annualized)
RISK_FREE_RATE = 0.05  # 5% (can adjust)

# Trading days per year (for annualization)
TRADING_DAYS_PER_YEAR = 365  # Crypto trades 24/7


# =============================================================================
# METRICS
# =============================================================================

@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics."""
    
    # Returns
    total_return_pct: float = 0.0
    total_return_usd: float = 0.0
    annualized_return_pct: float = 0.0
    
    # Risk metrics
    volatility_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_drawdown_pct: float = 0.0
    max_drawdown_duration_hours: float = 0.0
    
    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    
    # Trade metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    
    win_rate_pct: float = 0.0
    loss_rate_pct: float = 0.0
    
    avg_trade_pnl_pct: float = 0.0
    avg_trade_pnl_usd: float = 0.0
    
    avg_win_pct: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_pct: float = 0.0
    avg_loss_usd: float = 0.0
    
    largest_win_pct: float = 0.0
    largest_win_usd: float = 0.0
    largest_loss_pct: float = 0.0
    largest_loss_usd: float = 0.0
    
    # Ratios
    profit_factor: float = 0.0
    payoff_ratio: float = 0.0  # avg_win / avg_loss
    expectancy: float = 0.0     # Expected value per trade
    
    # Time metrics
    avg_hold_time_minutes: float = 0.0
    avg_win_hold_time_minutes: float = 0.0
    avg_loss_hold_time_minutes: float = 0.0
    
    # Streak
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_streak: int = 0
    current_streak_type: str = ""  # "win" or "loss"
    
    # Fees
    total_fees_usd: float = 0.0
    fees_pct_of_pnl: float = 0.0
    
    def to_dict(self) -> dict:
        """Convert to dict with rounded values."""
        result = {}
        
        for key, value in asdict(self).items():
            if isinstance(value, float):
                result[key] = round(value, 4)
            else:
                result[key] = value
        
        return result


@dataclass
class TimeBreakdown:
    """Performance breakdown for a time period."""
    
    period: str  # "2024-06-15", "2024-W25", "2024-06"
    period_type: str  # "daily", "weekly", "monthly"
    
    trades: int = 0
    wins: int = 0
    losses: int = 0
    
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    win_rate_pct: float = 0.0
    
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'period': self.period,
            'period_type': self.period_type,
            'trades': self.trades,
            'wins': self.wins,
            'losses': self.losses,
            'pnl_usd': round(self.pnl_usd, 2),
            'pnl_pct': round(self.pnl_pct, 2),
            'win_rate_pct': round(self.win_rate_pct, 1),
        }


@dataclass
class RegimeBreakdown:
    """Performance breakdown by market regime."""
    
    regime: str  # "bull", "bear", "range", "crash"
    
    trades: int = 0
    wins: int = 0
    losses: int = 0
    
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    win_rate_pct: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'regime': self.regime,
            'trades': self.trades,
            'wins': self.wins,
            'losses': self.losses,
            'pnl_usd': round(self.pnl_usd, 2),
            'pnl_pct': round(self.pnl_pct, 2),
            'win_rate_pct': round(self.win_rate_pct, 1),
            'avg_trade_pnl_pct': round(self.avg_trade_pnl_pct, 2),
        }


@dataclass 
class SymbolBreakdown:
    """Performance breakdown by symbol."""
    
    symbol: str
    
    trades: int = 0
    wins: int = 0
    losses: int = 0
    
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    win_rate_pct: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    
    total_volume_usd: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'trades': self.trades,
            'wins': self.wins,
            'losses': self.losses,
            'pnl_usd': round(self.pnl_usd, 2),
            'win_rate_pct': round(self.win_rate_pct, 1),
            'avg_trade_pnl_pct': round(self.avg_trade_pnl_pct, 2),
            'total_volume_usd': round(self.total_volume_usd, 2),
        }


# =============================================================================
# PAPER STATS
# =============================================================================

class PaperStats:
    """
    Calculate and report paper trading statistics.
    
    Usage:
        stats = PaperStats(portfolio)
        
        # Get metrics
        metrics = stats.get_performance_metrics()
        
        # Get breakdowns
        daily = stats.get_daily_breakdown()
        by_symbol = stats.get_symbol_breakdown()
        by_regime = stats.get_regime_breakdown()
        
        # Export
        stats.export_csv("trades.csv")
        
        # Print report
        print(stats.format_report())
    """
    
    def __init__(self, portfolio: PaperPortfolio):
        """
        Initialize stats calculator.
        
        Args:
            portfolio: Paper portfolio instance
        """
        self.portfolio = portfolio
        
        # Cache
        self._metrics_cache: Optional[PerformanceMetrics] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=30)
    
    # =========================================================================
    # MAIN METRICS
    # =========================================================================
    
    def get_performance_metrics(self, force_recalc: bool = False) -> PerformanceMetrics:
        """
        Calculate comprehensive performance metrics.
        
        Args:
            force_recalc: Force recalculation (ignore cache)
        
        Returns:
            PerformanceMetrics
        """
        # Check cache
        if not force_recalc and self._metrics_cache:
            if datetime.now() - self._cache_time < self._cache_ttl:
                return self._metrics_cache
        
        metrics = PerformanceMetrics()
        trades = self.portfolio._trade_results
        
        if not trades:
            return metrics
        
        # === BASIC COUNTS ===
        metrics.total_trades = len(trades)
        metrics.winning_trades = sum(1 for t in trades if t.net_pnl_usd > 0)
        metrics.losing_trades = sum(1 for t in trades if t.net_pnl_usd < 0)
        metrics.breakeven_trades = sum(1 for t in trades if abs(t.net_pnl_usd) < 0.01)
        
        # === WIN/LOSS RATES ===
        if metrics.total_trades > 0:
            metrics.win_rate_pct = (metrics.winning_trades / metrics.total_trades) * 100
            metrics.loss_rate_pct = (metrics.losing_trades / metrics.total_trades) * 100
        
        # === RETURNS ===
        metrics.total_return_usd = self.portfolio.total_pnl
        metrics.total_return_pct = self.portfolio.total_pnl_pct
        
        # Annualized return
        if trades:
            first_trade = min(t.entry_time for t in trades)
            last_trade = max(t.exit_time for t in trades)
            days_trading = max(1, (last_trade - first_trade).days)
            
            if days_trading > 0:
                daily_return = metrics.total_return_pct / days_trading
                metrics.annualized_return_pct = daily_return * TRADING_DAYS_PER_YEAR
        
        # === P&L STATS ===
        pnl_values = [t.net_pnl_usd for t in trades]
        pnl_pcts = [t.pnl_pct for t in trades]
        
        metrics.avg_trade_pnl_usd = sum(pnl_values) / len(pnl_values)
        metrics.avg_trade_pnl_pct = sum(pnl_pcts) / len(pnl_pcts)
        
        # Wins
        wins = [t for t in trades if t.net_pnl_usd > 0]
        if wins:
            metrics.avg_win_usd = sum(t.net_pnl_usd for t in wins) / len(wins)
            metrics.avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins)
            metrics.largest_win_usd = max(t.net_pnl_usd for t in wins)
            metrics.largest_win_pct = max(t.pnl_pct for t in wins)
        
        # Losses
        losses = [t for t in trades if t.net_pnl_usd < 0]
        if losses:
            metrics.avg_loss_usd = sum(t.net_pnl_usd for t in losses) / len(losses)
            metrics.avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses)
            metrics.largest_loss_usd = min(t.net_pnl_usd for t in losses)
            metrics.largest_loss_pct = min(t.pnl_pct for t in losses)
        
        # === RISK METRICS ===
        
        # Volatility (std dev of returns)
        if len(pnl_pcts) > 1:
            mean = sum(pnl_pcts) / len(pnl_pcts)
            variance = sum((x - mean) ** 2 for x in pnl_pcts) / len(pnl_pcts)
            metrics.volatility_pct = math.sqrt(variance)
        
        # Max drawdown
        metrics.max_drawdown_pct = self.portfolio.max_drawdown_pct
        
        # === RISK-ADJUSTED RETURNS ===
        
        # Sharpe Ratio
        if metrics.volatility_pct > 0:
            excess_return = metrics.annualized_return_pct - (RISK_FREE_RATE * 100)
            annualized_vol = metrics.volatility_pct * math.sqrt(TRADING_DAYS_PER_YEAR)
            metrics.sharpe_ratio = excess_return / annualized_vol if annualized_vol > 0 else 0
        
        # Sortino Ratio (uses downside deviation)
        downside_returns = [r for r in pnl_pcts if r < 0]
        if downside_returns:
            downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_deviation = math.sqrt(downside_variance)
            
            if downside_deviation > 0:
                annualized_downside = downside_deviation * math.sqrt(TRADING_DAYS_PER_YEAR)
                excess_return = metrics.annualized_return_pct - (RISK_FREE_RATE * 100)
                metrics.sortino_ratio = excess_return / annualized_downside
        
        # Calmar Ratio
        if metrics.max_drawdown_pct > 0:
            metrics.calmar_ratio = metrics.annualized_return_pct / metrics.max_drawdown_pct
        
        # === RATIOS ===
        
        # Profit Factor
        gross_profit = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
        gross_loss = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd < 0))
        
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            metrics.profit_factor = float('inf')
        
        # Payoff Ratio
        if metrics.avg_loss_usd != 0:
            metrics.payoff_ratio = abs(metrics.avg_win_usd / metrics.avg_loss_usd)
        
        # Expectancy
        metrics.expectancy = (
            (metrics.win_rate_pct / 100) * metrics.avg_win_usd +
            (metrics.loss_rate_pct / 100) * metrics.avg_loss_usd
        )
        
        # === TIME METRICS ===
        hold_times = [t.hold_time_minutes for t in trades]
        metrics.avg_hold_time_minutes = sum(hold_times) / len(hold_times)
        
        if wins:
            metrics.avg_win_hold_time_minutes = sum(t.hold_time_minutes for t in wins) / len(wins)
        
        if losses:
            metrics.avg_loss_hold_time_minutes = sum(t.hold_time_minutes for t in losses) / len(losses)
        
        # === STREAKS ===
        metrics.max_consecutive_wins, metrics.max_consecutive_losses = self._calculate_streaks(trades)
        metrics.current_streak, metrics.current_streak_type = self._current_streak(trades)
        
        # === FEES ===
        metrics.total_fees_usd = self.portfolio._total_fees_paid
        
        if abs(metrics.total_return_usd) > 0:
            metrics.fees_pct_of_pnl = (metrics.total_fees_usd / abs(metrics.total_return_usd)) * 100
        
        # Cache
        self._metrics_cache = metrics
        self._cache_time = datetime.now()
        
        return metrics
    
    def _calculate_streaks(self, trades: List[TradeResult]) -> Tuple[int, int]:
        """Calculate max consecutive wins and losses."""
        if not trades:
            return 0, 0
        
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in sorted(trades, key=lambda t: t.exit_time):
            if trade.net_pnl_usd > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif trade.net_pnl_usd < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0
        
        return max_wins, max_losses
    
    def _current_streak(self, trades: List[TradeResult]) -> Tuple[int, str]:
        """Calculate current streak."""
        if not trades:
            return 0, ""
        
        sorted_trades = sorted(trades, key=lambda t: t.exit_time, reverse=True)
        
        if sorted_trades[0].net_pnl_usd > 0:
            streak_type = "win"
            streak = 0
            for t in sorted_trades:
                if t.net_pnl_usd > 0:
                    streak += 1
                else:
                    break
        elif sorted_trades[0].net_pnl_usd < 0:
            streak_type = "loss"
            streak = 0
            for t in sorted_trades:
                if t.net_pnl_usd < 0:
                    streak += 1
                else:
                    break
        else:
            return 0, ""
        
        return streak, streak_type
    
    # =========================================================================
    # BREAKDOWNS
    # =========================================================================
    
    def get_daily_breakdown(self) -> List[TimeBreakdown]:
        """Get daily performance breakdown."""
        return self._time_breakdown('daily')
    
    def get_weekly_breakdown(self) -> List[TimeBreakdown]:
        """Get weekly performance breakdown."""
        return self._time_breakdown('weekly')
    
    def get_monthly_breakdown(self) -> List[TimeBreakdown]:
        """Get monthly performance breakdown."""
        return self._time_breakdown('monthly')
    
    def _time_breakdown(self, period_type: str) -> List[TimeBreakdown]:
        """Calculate time-based breakdown."""
        trades = self.portfolio._trade_results
        
        if not trades:
            return []
        
        # Group trades by period
        periods: Dict[str, List[TradeResult]] = defaultdict(list)
        
        for trade in trades:
            if period_type == 'daily':
                key = trade.exit_time.strftime('%Y-%m-%d')
            elif period_type == 'weekly':
                key = trade.exit_time.strftime('%Y-W%W')
            else:  # monthly
                key = trade.exit_time.strftime('%Y-%m')
            
            periods[key].append(trade)
        
        # Calculate stats for each period
        breakdowns = []
        
        for period, period_trades in sorted(periods.items()):
            wins = sum(1 for t in period_trades if t.net_pnl_usd > 0)
            losses = sum(1 for t in period_trades if t.net_pnl_usd < 0)
            pnl = sum(t.net_pnl_usd for t in period_trades)
            
            breakdown = TimeBreakdown(
                period=period,
                period_type=period_type,
                trades=len(period_trades),
                wins=wins,
                losses=losses,
                pnl_usd=pnl,
                pnl_pct=(pnl / self.portfolio._initial_balance) * 100,
                win_rate_pct=(wins / len(period_trades) * 100) if period_trades else 0
            )
            
            breakdowns.append(breakdown)
        
        return breakdowns
    
    def get_regime_breakdown(self) -> List[RegimeBreakdown]:
        """Get performance breakdown by market regime."""
        trades = self.portfolio._trade_results
        
        if not trades:
            return []
        
        # Group by regime
        regimes: Dict[str, List[TradeResult]] = defaultdict(list)
        
        for trade in trades:
            regime = trade.signal_regime if hasattr(trade, 'signal_regime') else 'unknown'
            regimes[regime].append(trade)
        
        # Calculate stats
        breakdowns = []
        
        for regime, regime_trades in sorted(regimes.items()):
            wins = sum(1 for t in regime_trades if t.net_pnl_usd > 0)
            losses = sum(1 for t in regime_trades if t.net_pnl_usd < 0)
            pnl = sum(t.net_pnl_usd for t in regime_trades)
            pnl_pct = sum(t.pnl_pct for t in regime_trades)
            
            breakdown = RegimeBreakdown(
                regime=regime,
                trades=len(regime_trades),
                wins=wins,
                losses=losses,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                win_rate_pct=(wins / len(regime_trades) * 100) if regime_trades else 0,
                avg_trade_pnl_pct=pnl_pct / len(regime_trades) if regime_trades else 0
            )
            
            breakdowns.append(breakdown)
        
        return breakdowns
    
    def get_symbol_breakdown(self) -> List[SymbolBreakdown]:
        """Get performance breakdown by symbol."""
        trades = self.portfolio._trade_results
        
        if not trades:
            return []
        
        # Group by symbol
        symbols: Dict[str, List[TradeResult]] = defaultdict(list)
        
        for trade in trades:
            symbols[trade.symbol].append(trade)
        
        # Calculate stats
        breakdowns = []
        
        for symbol, symbol_trades in sorted(symbols.items()):
            wins = sum(1 for t in symbol_trades if t.net_pnl_usd > 0)
            losses = sum(1 for t in symbol_trades if t.net_pnl_usd < 0)
            pnl = sum(t.net_pnl_usd for t in symbol_trades)
            pnl_pct = sum(t.pnl_pct for t in symbol_trades)
            volume = sum(t.size_usd for t in symbol_trades)
            
            breakdown = SymbolBreakdown(
                symbol=symbol,
                trades=len(symbol_trades),
                wins=wins,
                losses=losses,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                win_rate_pct=(wins / len(symbol_trades) * 100) if symbol_trades else 0,
                avg_trade_pnl_pct=pnl_pct / len(symbol_trades) if symbol_trades else 0,
                total_volume_usd=volume
            )
            
            breakdowns.append(breakdown)
        
        # Sort by P&L
        breakdowns.sort(key=lambda x: x.pnl_usd, reverse=True)
        
        return breakdowns
    
    def get_close_reason_breakdown(self) -> Dict[str, dict]:
        """Get breakdown by close reason."""
        trades = self.portfolio._trade_results
        
        if not trades:
            return {}
        
        reasons: Dict[str, List[TradeResult]] = defaultdict(list)
        
        for trade in trades:
            reason = trade.close_reason.value if hasattr(trade.close_reason, 'value') else str(trade.close_reason)
            reasons[reason].append(trade)
        
        result = {}
        
        for reason, reason_trades in reasons.items():
            wins = sum(1 for t in reason_trades if t.net_pnl_usd > 0)
            pnl = sum(t.net_pnl_usd for t in reason_trades)
            
            result[reason] = {
                'count': len(reason_trades),
                'wins': wins,
                'losses': len(reason_trades) - wins,
                'win_rate_pct': (wins / len(reason_trades) * 100) if reason_trades else 0,
                'total_pnl_usd': round(pnl, 2),
                'avg_pnl_usd': round(pnl / len(reason_trades), 2) if reason_trades else 0
            }
        
        return result
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    def get_summary(self) -> dict:
        """Get complete statistics summary."""
        metrics = self.get_performance_metrics()
        
        return {
            'portfolio': self.portfolio.get_stats(),
            'metrics': metrics.to_dict(),
            'by_regime': [b.to_dict() for b in self.get_regime_breakdown()],
            'by_symbol': [b.to_dict() for b in self.get_symbol_breakdown()[:10]],  # Top 10
            'by_close_reason': self.get_close_reason_breakdown(),
            'daily': [b.to_dict() for b in self.get_daily_breakdown()[-30:]],  # Last 30 days
        }
    
    # =========================================================================
    # EXPORT
    # =========================================================================
    
    def export_csv(self, path: str):
        """Export trades to CSV."""
        trades = self.portfolio._trade_results
        
        if not trades:
            log.warning("No trades to export")
            return
        
        with open(path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].to_dict().keys())
            writer.writeheader()
            
            for trade in trades:
                writer.writerow(trade.to_dict())
        
        log.info(f"Exported {len(trades)} trades to {path}")
    
    def export_json(self, path: str):
        """Export full stats to JSON."""
        summary = self.get_summary()
        
        with open(path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        log.info(f"Exported stats to {path}")
    
    # =========================================================================
    # FORMATTED REPORT
    # =========================================================================
    
    def format_report(self, detailed: bool = True) -> str:
        """
        Format statistics as readable report.
        
        Args:
            detailed: Include detailed breakdowns
        
        Returns:
            Formatted string
        """
        metrics = self.get_performance_metrics()
        portfolio = self.portfolio.get_stats()
        
        lines = []
        
        # Header
        lines.append("=" * 60)
        lines.append("📊 PAPER TRADING STATISTICS")
        lines.append("=" * 60)
        lines.append("")
        
        # Portfolio
        lines.append("💰 PORTFOLIO")
        lines.append("-" * 40)
        lines.append(f"  Initial Balance:   ${portfolio['initial_balance']:,.2f}")
        lines.append(f"  Current Balance:   ${portfolio['current_balance']:,.2f}")
        
        pnl_emoji = "🟢" if portfolio['total_pnl'] >= 0 else "🔴"
        lines.append(f"  Total P&L:         {pnl_emoji} ${portfolio['total_pnl']:+,.2f} ({portfolio['total_pnl_pct']:+.2f}%)")
        lines.append(f"  Max Drawdown:      {portfolio['max_drawdown_pct']:.2f}%")
        lines.append(f"  Fees Paid:         ${portfolio['total_fees_paid']:,.2f}")
        lines.append("")
        
        # Trade Stats
        lines.append("📈 TRADE STATISTICS")
        lines.append("-" * 40)
        lines.append(f"  Total Trades:      {metrics.total_trades}")
        lines.append(f"  Winning:           {metrics.winning_trades} ({metrics.win_rate_pct:.1f}%)")
        lines.append(f"  Losing:            {metrics.losing_trades} ({metrics.loss_rate_pct:.1f}%)")
        lines.append("")
        lines.append(f"  Avg Trade P&L:     ${metrics.avg_trade_pnl_usd:+.2f} ({metrics.avg_trade_pnl_pct:+.2f}%)")
        lines.append(f"  Avg Win:           ${metrics.avg_win_usd:+.2f} ({metrics.avg_win_pct:+.2f}%)")
        lines.append(f"  Avg Loss:          ${metrics.avg_loss_usd:+.2f} ({metrics.avg_loss_pct:+.2f}%)")
        lines.append("")
        lines.append(f"  Largest Win:       ${metrics.largest_win_usd:+.2f} ({metrics.largest_win_pct:+.2f}%)")
        lines.append(f"  Largest Loss:      ${metrics.largest_loss_usd:+.2f} ({metrics.largest_loss_pct:+.2f}%)")
        lines.append("")
        
        # Ratios
        lines.append("📊 PERFORMANCE RATIOS")
        lines.append("-" * 40)
        lines.append(f"  Profit Factor:     {metrics.profit_factor:.2f}")
        lines.append(f"  Payoff Ratio:      {metrics.payoff_ratio:.2f}")
        lines.append(f"  Expectancy:        ${metrics.expectancy:+.2f}")
        lines.append(f"  Sharpe Ratio:      {metrics.sharpe_ratio:.2f}")
        lines.append(f"  Sortino Ratio:     {metrics.sortino_ratio:.2f}")
        lines.append(f"  Calmar Ratio:      {metrics.calmar_ratio:.2f}")
        lines.append("")
        
        # Time
        lines.append("⏱️ TIME METRICS")
        lines.append("-" * 40)
        lines.append(f"  Avg Hold Time:     {metrics.avg_hold_time_minutes:.1f} min")
        lines.append(f"  Avg Win Duration:  {metrics.avg_win_hold_time_minutes:.1f} min")
        lines.append(f"  Avg Loss Duration: {metrics.avg_loss_hold_time_minutes:.1f} min")
        lines.append("")
        
        # Streaks
        lines.append("🔥 STREAKS")
        lines.append("-" * 40)
        lines.append(f"  Max Consecutive Wins:   {metrics.max_consecutive_wins}")
        lines.append(f"  Max Consecutive Losses: {metrics.max_consecutive_losses}")
        
        if metrics.current_streak > 0:
            streak_emoji = "🟢" if metrics.current_streak_type == "win" else "🔴"
            lines.append(f"  Current Streak:         {streak_emoji} {metrics.current_streak} {metrics.current_streak_type}s")
        lines.append("")
        
        # Detailed breakdowns
        if detailed:
            # By regime
            regime_breakdown = self.get_regime_breakdown()
            if regime_breakdown:
                lines.append("🌍 BY REGIME")
                lines.append("-" * 40)
                
                for rb in regime_breakdown:
                    emoji = "🟢" if rb.pnl_usd >= 0 else "🔴"
                    lines.append(f"  {rb.regime.upper():<10} {rb.trades:>3} trades | "
                               f"WR: {rb.win_rate_pct:>5.1f}% | "
                               f"P&L: {emoji} ${rb.pnl_usd:>+8.2f}")
                lines.append("")
            
            # Top symbols
            symbol_breakdown = self.get_symbol_breakdown()[:5]
            if symbol_breakdown:
                lines.append("🏆 TOP SYMBOLS")
                lines.append("-" * 40)
                
                for sb in symbol_breakdown:
                    emoji = "🟢" if sb.pnl_usd >= 0 else "🔴"
                    lines.append(f"  {sb.symbol:<12} {sb.trades:>3} trades | "
                               f"WR: {sb.win_rate_pct:>5.1f}% | "
                               f"P&L: {emoji} ${sb.pnl_usd:>+8.2f}")
                lines.append("")
            
            # By close reason
            reason_breakdown = self.get_close_reason_breakdown()
            if reason_breakdown:
                lines.append("🚪 BY EXIT REASON")
                lines.append("-" * 40)
                
                for reason, data in reason_breakdown.items():
                    emoji = "🟢" if data['total_pnl_usd'] >= 0 else "🔴"
                    lines.append(f"  {reason:<15} {data['count']:>3} trades | "
                               f"WR: {data['win_rate_pct']:>5.1f}% | "
                               f"P&L: {emoji} ${data['total_pnl_usd']:>+8.2f}")
                lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    # =========================================================================
    # CHARTS DATA
    # =========================================================================
    
    def get_equity_curve(self) -> List[Dict]:
        """
        Get equity curve data for charting.
        
        Returns:
            List of {timestamp, balance, pnl}
        """
        trades = sorted(self.portfolio._trade_results, key=lambda t: t.exit_time)
        
        curve = [{
            'timestamp': self.portfolio._created_at.isoformat(),
            'balance': self.portfolio._initial_balance,
            'pnl': 0.0
        }]
        
        running_balance = self.portfolio._initial_balance
        
        for trade in trades:
            running_balance += trade.net_pnl_usd
            
            curve.append({
                'timestamp': trade.exit_time.isoformat(),
                'balance': round(running_balance, 2),
                'pnl': round(trade.net_pnl_usd, 2)
            })
        
        return curve
    
    def get_drawdown_chart(self) -> List[Dict]:
        """
        Get drawdown chart data.
        
        Returns:
            List of {timestamp, drawdown_pct}
        """
        curve = self.get_equity_curve()
        
        if not curve:
            return []
        
        drawdown_data = []
        peak = curve[0]['balance']
        
        for point in curve:
            if point['balance'] > peak:
                peak = point['balance']
            
            dd = ((peak - point['balance']) / peak) * 100 if peak > 0 else 0
            
            drawdown_data.append({
                'timestamp': point['timestamp'],
                'drawdown_pct': round(dd, 2)
            })
        
        return drawdown_data
    
    def get_pnl_distribution(self, bins: int = 20) -> List[Dict]:
        """
        Get P&L distribution for histogram.
        
        Returns:
            List of {range, count}
        """
        trades = self.portfolio._trade_results
        
        if not trades:
            return []
        
        pnl_values = [t.pnl_pct for t in trades]
        
        min_pnl = min(pnl_values)
        max_pnl = max(pnl_values)
        bin_size = (max_pnl - min_pnl) / bins
        
        distribution = []
        
        for i in range(bins):
            lower = min_pnl + i * bin_size
            upper = lower + bin_size
            
            count = sum(1 for p in pnl_values if lower <= p < upper)
            
            distribution.append({
                'range_start': round(lower, 2),
                'range_end': round(upper, 2),
                'count': count
            })
        
        return distribution


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def calculate_stats(portfolio: PaperPortfolio) -> PaperStats:
    """Create stats calculator for portfolio."""
    return PaperStats(portfolio)


def print_stats(portfolio: PaperPortfolio, detailed: bool = True):
    """Print formatted stats report."""
    stats = PaperStats(portfolio)
    print(stats.format_report(detailed))