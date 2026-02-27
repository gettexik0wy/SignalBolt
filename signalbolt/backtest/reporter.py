"""
Backtest Reporter - formatting and export.

Handles:
- Console output (trade tables, summaries)
- CSV export
- JSON export
"""

import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from signalbolt.backtest.engine import BacktestResult
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.backtest.reporter")

# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
RESULTS_DIR = ROOT_DIR / "backtest_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


class BacktestReporter:
    """
    Format and export backtest results.

    Usage:
        reporter = BacktestReporter(result)
        reporter.print_summary()
        reporter.print_trades(limit=20)
        reporter.save_csv()
        reporter.save_json()
    """

    def __init__(self, result: BacktestResult):
        self.result = result
        self.fee_pct = result.config.total_fee_pct

    # =========================================================================
    # CONSOLE OUTPUT
    # =========================================================================

    def print_summary(self):
        """Print results summary to console."""
        r = self.result
        fee = self.fee_pct

        g = "\033[92m"  # green
        rd = "\033[91m"  # red
        y = "\033[93m"  # yellow
        c = "\033[96m"  # cyan
        b = "\033[1m"  # bold
        d = "\033[2m"  # dim
        rst = "\033[0m"  # reset

        pnl = r.total_pnl_pct()
        pnl_c = g if pnl > 0 else rd

        print(f"\n  {'═' * 64}")
        print(f"  {b}📊 BACKTEST SUMMARY{rst}")
        print(f"  {'═' * 64}")

        print(f"\n  {d}Symbol:{rst}    {b}{r.symbol}{rst}")
        print(f"  {d}Period:{rst}    {r.period_name}")
        print(f"  {d}Range:{rst}     {r.start_date} → {r.end_date}")
        print(f"  {d}Interval:{rst}  {r.interval}")

        print(f"\n  {'─' * 64}")
        print(f"  {b}💰 Capital{rst}")
        print(f"  {'─' * 64}")
        print(f"     Initial:        ${r.initial_balance:>12,.2f}")
        print(f"     Final:          ${r.final_balance:>12,.2f}")
        print(
            f"     P&L:            {pnl_c}{pnl:>+11.2f}%{rst}  (${r.total_pnl_usd():>+,.2f})"
        )
        print(f"     Max Drawdown:   {rd}{r.max_drawdown_pct():>11.2f}%{rst}")

        print(f"\n  {'─' * 64}")
        print(f"  {b}📊 Trades{rst}")
        print(f"  {'─' * 64}")
        print(f"     Total:          {r.total_trades():>8}")
        print(f"     Wins:           {r.winning_trades():>8}  ({r.winrate():.1f}%)")
        print(f"     Losses:         {r.losing_trades():>8}")
        print(f"     Profit Factor:  {r.profit_factor():>8.2f}")

        print(f"\n  {'─' * 64}")
        print(f"  {b}📈 Performance{rst}")
        print(f"  {'─' * 64}")
        print(f"     Avg Trade:      {r.avg_trade_pnl():>+8.2f}%")
        print(f"     Avg Winner:     {g}{r.avg_winner_pnl():>+8.2f}%{rst}")
        print(f"     Avg Loser:      {rd}{r.avg_loser_pnl():>+8.2f}%{rst}")
        print(f"     Best Trade:     {g}{r.best_trade():>+8.2f}%{rst}")
        print(f"     Worst Trade:    {rd}{r.worst_trade():>+8.2f}%{rst}")
        print(f"     Avg Hold Time:  {r.avg_hold_time():>8.0f} min")

        print(f"\n  {'─' * 64}")
        print(f"  {b}📐 Risk-Adjusted{rst}")
        print(f"  {'─' * 64}")

        sharpe = r.sharpe_ratio()
        sortino = r.sortino_ratio()
        exp = r.expectancy()

        sharpe_c = g if sharpe > 0 else rd
        sortino_c = g if sortino > 0 else rd
        exp_c = g if exp > 0 else rd

        print(f"     Sharpe Ratio:   {sharpe_c}{sharpe:>8.2f}{rst}")
        print(f"     Sortino Ratio:  {sortino_c}{sortino:>8.2f}{rst}")
        print(f"     Expectancy:     {exp_c}{exp:>+8.2f}%{rst}")
        print(f"     Max Consec W:   {r.max_consecutive_wins():>8}")
        print(f"     Max Consec L:   {r.max_consecutive_losses():>8}")

        # Signals
        print(f"\n  {'─' * 64}")
        print(f"  {b}📡 Signals{rst}")
        print(f"  {'─' * 64}")
        print(f"     Generated:      {r.signals_generated:>8}")
        print(f"     Executed:       {r.signals_executed:>8}")
        print(f"     Rejected Score: {r.signals_rejected_score:>8}")
        print(f"     Rejected Spread:{r.signals_rejected_spread:>8}")

        # Costs
        if r.total_slippage_cost > 0 or r.total_spread_cost > 0:
            print(f"\n  {'─' * 64}")
            print(f"  {b}💸 Execution Costs{rst}")
            print(f"  {'─' * 64}")
            print(f"     Slippage:       ${r.total_slippage_cost:>10.2f}")
            print(f"     Spread:         ${r.total_spread_cost:>10.2f}")
            total_costs = r.total_slippage_cost + r.total_spread_cost
            print(f"     Total Costs:    ${total_costs:>10.2f}")

        # Regime
        if r.regime_distribution:
            print(f"\n  {'─' * 64}")
            print(f"  {b}🌍 Regime Distribution{rst}")
            print(f"  {'─' * 64}")

            total_candles = sum(r.regime_distribution.values())
            regime_perf = r.performance_by_regime()

            for regime in ["bull", "bear", "range", "crash"]:
                candles = r.regime_distribution.get(regime, 0)
                if candles == 0:
                    continue

                pct = (candles / total_candles * 100) if total_candles > 0 else 0

                regime_info = f"     {regime.upper():<8} {pct:5.1f}%"

                if regime in regime_perf:
                    stats = regime_perf[regime]
                    rp = stats["total_pnl"]
                    rc = g if rp > 0 else rd
                    regime_info += (
                        f"  │  {stats['trades']:3d} trades  "
                        f"WR: {stats['winrate']:5.1f}%  "
                        f"{rc}{rp:+.2f}%{rst}"
                    )

                print(regime_info)

        # Timing
        print(f"\n  {d}⏱️  Completed in {r.backtest_duration_sec:.1f}s{rst}")
        print(f"  {'═' * 64}\n")

    def print_trades(self, limit: int = 20):
        """Print trade history table."""
        trades = self.result.trades
        fee = self.fee_pct

        if not trades:
            print(f"\n  No trades to display.")
            return

        g = "\033[92m"
        rd = "\033[91m"
        b = "\033[1m"
        d = "\033[2m"
        rst = "\033[0m"

        shown = trades[:limit]

        print(f"\n  {'═' * 100}")
        print(f"  {b}📋 TRADE HISTORY{rst} ({len(shown)} of {len(trades)})")
        print(f"  {'═' * 100}")

        # Header
        print(
            f"\n  {'#':>4}  {'Regime':<8} {'Entry Time':<17} {'Entry $':>12} "
            f"{'Exit $':>12} {'P&L':>8} {'Net P&L':>8} {'Exit Reason':<22} {'Hold':>6}"
        )
        print(f"  {'-' * 98}")

        for t in shown:
            pnl = t.pnl_pct()
            net_pnl = t.net_pnl_pct(fee)
            pnl_c = g if net_pnl > 0 else rd

            entry_time = t.entry_time.strftime("%m-%d %H:%M") if t.entry_time else ""
            hold = f"{t.hold_time_minutes():.0f}m"

            print(
                f"  {t.trade_id:>4}  {t.entry_regime:<8} {entry_time:<17} "
                f"${t.entry_price:>11,.2f} ${t.exit_price:>11,.2f} "
                f"{pnl_c}{pnl:>+7.2f}%{rst} {pnl_c}{net_pnl:>+7.2f}%{rst} "
                f"{t.exit_reason:<22} {hold:>6}"
            )

        # Summary line
        total_net = sum(t.net_pnl_pct(fee) for t in trades)
        winners = sum(1 for t in trades if t.is_winner(fee))
        total_c = g if total_net > 0 else rd

        print(f"  {'-' * 98}")
        print(
            f"  {'':>4}  {'TOTAL':<8} {'':<17} {'':<13} {'':<13} "
            f"{'':<9} {total_c}{total_net:>+7.2f}%{rst} "
            f"{'':>22} {d}WR: {winners}/{len(trades)}{rst}"
        )

        if len(trades) > limit:
            print(f"\n  {d}... {len(trades) - limit} more trades not shown{rst}")

        print()

    # =========================================================================
    # EXPORT
    # =========================================================================

    def save_csv(self, output_dir: Optional[Path] = None) -> str:
        """
        Save trade list to CSV.

        Args:
            output_dir: Output directory (default: backtest_results/)

        Returns:
            Path to saved file
        """
        output_dir = output_dir or RESULTS_DIR
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        symbol = self.result.symbol.replace("/", "_")
        filename = f"trades_{symbol}_{timestamp}.csv"
        filepath = output_dir / filename

        fee = self.fee_pct

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Header
            writer.writerow(
                [
                    "trade_id",
                    "symbol",
                    "direction",
                    "regime",
                    "entry_time",
                    "entry_price",
                    "entry_score",
                    "exit_time",
                    "exit_price",
                    "exit_reason",
                    "size_usd",
                    "pnl_pct",
                    "net_pnl_pct",
                    "net_pnl_usd",
                    "highest_pnl_pct",
                    "hold_time_min",
                    "trailing_active",
                    "exit_regime",
                ]
            )

            # Rows
            for t in self.result.trades:
                writer.writerow(
                    [
                        t.trade_id,
                        t.symbol,
                        t.direction,
                        t.entry_regime,
                        t.entry_time.isoformat() if t.entry_time else "",
                        round(t.entry_price, 8),
                        round(t.entry_score, 1),
                        t.exit_time.isoformat() if t.exit_time else "",
                        round(t.exit_price, 8),
                        t.exit_reason,
                        round(t.size_usd, 2),
                        round(t.pnl_pct(), 2),
                        round(t.net_pnl_pct(fee), 2),
                        round(t.net_pnl_usd(fee), 2),
                        round(t.highest_pnl_pct, 2),
                        round(t.hold_time_minutes(), 1),
                        t.trailing_active,
                        t.exit_regime,
                    ]
                )

        log.info(f"CSV saved: {filepath}")
        return str(filepath)

    def save_json(self, output_dir: Optional[Path] = None) -> str:
        """
        Save full results to JSON.

        Args:
            output_dir: Output directory (default: backtest_results/)

        Returns:
            Path to saved file
        """
        output_dir = output_dir or RESULTS_DIR
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        symbol = self.result.symbol.replace("/", "_")
        filename = f"backtest_{symbol}_{timestamp}.json"
        filepath = output_dir / filename

        data = self.result.to_dict()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)

        log.info(f"JSON saved: {filepath}")
        return str(filepath)
