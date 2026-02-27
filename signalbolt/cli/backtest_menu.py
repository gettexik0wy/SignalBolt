"""
Backtest menu - historical strategy testing.

Features:
- Run backtest with config selection
- Preset periods (Bear 2022, Bull 2021, etc.)
- View trade history
- Export results (CSV, JSON, HTML)
- PRO features (Monte Carlo, Walk-Forward, Multi-Symbol, Compare Configs)
"""

import time
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from signalbolt.backtest import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult,
    BacktestReporter,
    DataManager,
    # PRO feature flags
    PRO_AVAILABLE,
    PRO_MONTE_CARLO,
    PRO_WALK_FORWARD,
    PRO_MULTI_SYMBOL,
    PRO_MULTI_CONFIG,
    PRO_BENCHMARK,
    # HTML export
    HTML_EXPORT_AVAILABLE,
)
from signalbolt.cli.utils import (
    clear_screen,
    print_header,
    print_divider,
    print_table,
    input_yes_no,
    input_string,
    input_number,
    green,
    red,
    yellow,
    cyan,
    bold,
    dim,
    format_usd,
    format_pct,
    format_pct_colored,
)
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.cli.backtest_menu")


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
CONFIGS_DIR = ROOT_DIR / "configs"
RESULTS_DIR = ROOT_DIR / "backtest_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# PRESET PERIODS
# =============================================================================

PRESET_PERIODS = [
    ("Bear Market 2022", "2022-05-01", "2022-12-31", "🔴", "bear"),
    ("Bull Run 2021", "2021-01-01", "2021-04-30", "🟢", "bull"),
    ("Range 2023", "2023-01-01", "2023-10-31", "🟡", "range"),
    ("LUNA Crash", "2022-05-05", "2022-05-15", "💥", "crash"),
    ("FTX Collapse", "2022-11-01", "2022-11-20", "💥", "crash"),
    ("COVID Crash", "2020-03-01", "2020-03-31", "💥", "crash"),
    ("2024 H1 (Unseen)", "2024-01-01", "2024-06-30", "🔬", "mixed"),
]

SYMBOL_DATA_START = {
    "BTCUSDT": "2019-01-01",
    "ETHUSDT": "2019-01-01",
    "BNBUSDT": "2019-01-01",
    "SOLUSDT": "2020-08-01",
    "XRPUSDT": "2019-01-01",
    "DOGEUSDT": "2020-07-01",
    "ADAUSDT": "2019-01-01",
    "AVAXUSDT": "2020-09-01",
    "LINKUSDT": "2019-01-01",
    "MATICUSDT": "2020-04-01",
}


# =============================================================================
# STATE
# =============================================================================

_last_result: Optional[BacktestResult] = None


# =============================================================================
# MAIN MENU
# =============================================================================


def run_backtest_menu():
    """Main backtest menu entry point."""
    global _last_result

    while True:
        try:
            _show_main_menu()
        except KeyboardInterrupt:
            print(f"\n\n{yellow('Returning to main menu...')}")
            time.sleep(0.5)
            break
        except Exception as e:
            print(f"\n{red(f'Error: {e}')}")
            import traceback

            traceback.print_exc()
            input("\nPress Enter to continue...")


def _show_main_menu():
    """Show main backtest menu."""
    clear_screen()

    today = datetime.now().strftime("%Y-%m-%d")

    # PRO status indicator
    pro_tag = f" {green('PRO ✓')}" if PRO_AVAILABLE else ""

    print(f"\n{'═' * 70}")
    print(f"  📈 {bold('BACKTEST ENGINE')}{pro_tag} │ Today: {cyan(today)}")
    print(f"{'═' * 70}\n")

    # Core features
    print(
        f"  {cyan('[1]')} 🚀 Run Backtest             {dim('(Select config, symbol, period)')}"
    )
    print(
        f"  {cyan('[2]')} 📋 Quick Presets            {dim('(Bear 2022, Bull 2021, etc.)')}"
    )
    print(f"  {cyan('[3]')} 📁 View Last Result         {dim('(Trade list, stats)')}")
    print(f"  {cyan('[4]')} 💾 Export Results           {dim('(CSV, JSON, HTML)')}")
    print()

    # PRO features
    print(f"  {dim('─' * 60)}")
    pro_label = green("PRO") if PRO_AVAILABLE else yellow("PRO 🔒")
    print(f"  {pro_label} {dim('Features:')}")

    mc_icon = "✓" if PRO_MONTE_CARLO else "🔒"
    wf_icon = "✓" if PRO_WALK_FORWARD else "🔒"
    ms_icon = "✓" if PRO_MULTI_SYMBOL else "🔒"
    cc_icon = "✓" if PRO_MULTI_CONFIG else "🔒"

    print(
        f"  {cyan('[5]')} 🎲 Monte Carlo         {mc_icon}  {dim('(Test strategy robustness)')}"
    )
    print(
        f"  {cyan('[6]')} 📈 Walk-Forward        {wf_icon}  {dim('(Detect overfitting)')}"
    )
    print(f"  {cyan('[7]')} 🔬 Multi-Symbol        {ms_icon}  {dim('(Batch testing)')}")
    print(
        f"  {cyan('[8]')} ⚖️  Compare Configs     {cc_icon}  {dim('(Side-by-side ranking)')}"
    )
    print()
    print(f"  {dim('[0]')} 🚪 Back to Main Menu")
    print()

    choice = input(f"  {bold('Select option')}: ").strip()

    if choice == "0" or not choice:
        raise KeyboardInterrupt
    elif choice == "1":
        _run_backtest_wizard()
    elif choice == "2":
        _quick_presets()
    elif choice == "3":
        _view_last_result()
    elif choice == "4":
        _export_results()
    elif choice == "5":
        _monte_carlo_menu()
    elif choice == "6":
        _walk_forward_menu()
    elif choice == "7":
        _multi_symbol_menu()
    elif choice == "8":
        _compare_configs_menu()


# =============================================================================
# RUN BACKTEST WIZARD
# =============================================================================


def _run_backtest_wizard():
    """Full backtest wizard with config selection."""
    global _last_result

    clear_screen()
    print_header("RUN BACKTEST")

    # STEP 1: CONFIG
    print(f"\n  {cyan('STEP 1:')} {bold('Select Configuration')}")
    print(f"  {'─' * 60}")

    configs = _get_available_configs()

    if not configs:
        print(f"\n  {red('No config files found in configs/ directory')}")
        input("\n  Press Enter to continue...")
        return

    print()
    for i, cfg in enumerate(configs, 1):
        desc = _get_config_description(cfg)
        print(f"    {cyan(str(i))}. {cfg:<30} {dim(desc)}")

    print()
    config_choice = input(f"  Config [1-{len(configs)}]: ").strip()

    try:
        config_idx = int(config_choice) - 1
        if 0 <= config_idx < len(configs):
            selected_config = configs[config_idx]
        else:
            selected_config = configs[0]
    except (ValueError, IndexError):
        selected_config = configs[0]

    config_path = CONFIGS_DIR / selected_config
    print(f"\n  {green('✓')} Config: {bold(selected_config)}")

    try:
        config = BacktestConfig.from_yaml(str(config_path))
        print(
            f"     SL: {config.hard_sl_pct}% | BE: {config.be_activation_pct}% | "
            f"Trail: {config.trail_distance_pct}% | Min Score: {config.min_signal_score}"
        )
    except Exception as e:
        print(f"  {red(f'Error loading config: {e}')}")
        input("\n  Press Enter to continue...")
        return

    # STEP 2: SYMBOL
    symbol = _select_symbol()
    if symbol is None:
        return

    earliest = SYMBOL_DATA_START.get(symbol, "2020-01-01")
    latest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"\n  {green('✓')} Symbol: {bold(symbol)}")
    print(f"     Data available: {earliest} → {latest}")

    # STEP 3: PERIOD
    start_date, end_date, period_name = _select_period(earliest, latest)
    if start_date is None:
        return

    days = (
        datetime.strptime(end_date, "%Y-%m-%d")
        - datetime.strptime(start_date, "%Y-%m-%d")
    ).days
    print(f"\n  {green('✓')} Period: {bold(period_name)} ({days} days)")

    # STEP 4: SETTINGS
    print(f"\n  {cyan('STEP 4:')} {bold('Settings')}")
    print(f"  {'─' * 60}")

    initial_balance = input_number(
        "\n  Initial balance ($)", default=1000.0, min_val=100, max_val=1000000
    )
    adaptive = input_yes_no("  Use adaptive regime detection?", default=True)
    config.adaptive_regime = adaptive

    # CONFIRM
    print(f"\n  {'═' * 60}")
    print(f"  {bold('BACKTEST CONFIGURATION')}")
    print(f"  {'─' * 60}")
    print(f"    Config:      {yellow(selected_config)}")
    print(f"    Symbol:      {bold(symbol)}")
    print(f"    Period:      {start_date} → {end_date} ({days} days)")
    print(f"    Balance:     {format_usd(initial_balance)}")
    print(f"    Adaptive:    {green('Yes') if adaptive else red('No')}")
    print(f"  {'═' * 60}")

    if not input_yes_no("\n  Start backtest?", default=True):
        return

    # RUN
    print(f"\n\n{'═' * 70}")
    print(f"{'RUNNING BACKTEST':^70}")
    print(f"{'═' * 70}\n")

    try:
        engine = BacktestEngine(config, verbose=True)

        result = engine.run(
            symbol=symbol,
            interval="5m",
            start_date=start_date,
            end_date=end_date,
            period_name=period_name,
            initial_balance=initial_balance,
        )

        _last_result = result

        if result.total_trades() > 0:
            reporter = BacktestReporter(result)
            reporter.print_trades(limit=20)

        # Save
        if input_yes_no("  Save results?", default=True):
            _save_result(result)
            if HTML_EXPORT_AVAILABLE:
                if input_yes_no("  Generate HTML report with charts?", default=True):
                    _export_html(result)

    except Exception as e:
        print(f"\n  {red(f'Backtest error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# QUICK PRESETS
# =============================================================================


def _quick_presets():
    """Run backtest on preset period."""
    global _last_result

    clear_screen()
    print_header("QUICK PRESETS")

    print(f"\n  📊 {bold('Available Periods')}")
    print(f"  {'─' * 60}")

    for i, (name, start, end, emoji, _regime) in enumerate(PRESET_PERIODS, 1):
        days = (
            datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")
        ).days
        print(
            f"    {cyan(str(i))}. {emoji} {name:<25} {dim(f'{start} → {end}')} ({days}d)"
        )

    print()
    choice = input(f"  Select period [1-{len(PRESET_PERIODS)}]: ").strip()

    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(PRESET_PERIODS)):
            return
        name, start_date, end_date, _, _ = PRESET_PERIODS[idx]
    except (ValueError, IndexError):
        return

    # Config
    configs = _get_available_configs()
    config_path = _quick_select_config(configs)
    if config_path is None:
        return

    symbol = input_string("\n  Symbol", default="BTCUSDT").upper()
    initial_balance = input_number("  Initial balance ($)", default=1000.0)

    print(f"\n  {cyan('Running backtest...')}\n")

    try:
        config = BacktestConfig.from_yaml(config_path)
        engine = BacktestEngine(config, verbose=True)

        result = engine.run(
            symbol=symbol,
            interval="5m",
            start_date=start_date,
            end_date=end_date,
            period_name=name,
            initial_balance=initial_balance,
        )

        _last_result = result

        if result.total_trades() > 0:
            reporter = BacktestReporter(result)
            reporter.print_trades(limit=15)

    except Exception as e:
        print(f"\n  {red(f'Error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# VIEW LAST RESULT
# =============================================================================


def _view_last_result():
    """View last backtest result details."""
    global _last_result

    clear_screen()
    print_header("LAST BACKTEST RESULT")

    if _last_result is None:
        print(f"\n  {yellow('No backtest result available.')}")
        print(f"  {dim('Run a backtest first.')}")
        input("\n  Press Enter to continue...")
        return

    result = _last_result
    reporter = BacktestReporter(result)
    reporter.print_summary()

    print(f"\n  {cyan('[1]')} View trade list")
    print(f"  {cyan('[2]')} View regime breakdown")
    print(f"  {cyan('[3]')} View exit reason breakdown")
    print(f"  {cyan('[4]')} Export CSV")
    print(f"  {cyan('[5]')} Export JSON")

    if HTML_EXPORT_AVAILABLE:
        print(f"  {cyan('[6]')} Export HTML report")

    print(f"\n  {dim('[0]')} Back")
    print()

    choice = input(f"  {bold('Select option')}: ").strip()

    if choice == "1":
        limit = int(input_number("  How many trades to show?", default=50, min_val=1))
        reporter.print_trades(limit=limit)
    elif choice == "2":
        _show_regime_breakdown(result)
    elif choice == "3":
        _show_exit_breakdown(result)
    elif choice == "4":
        path = reporter.save_csv()
        print(f"\n  {green('✓')} Saved: {path}")
    elif choice == "5":
        path = reporter.save_json()
        print(f"\n  {green('✓')} Saved: {path}")
    elif choice == "6" and HTML_EXPORT_AVAILABLE:
        _export_html(result)

    input("\n  Press Enter to continue...")


def _show_regime_breakdown(result: BacktestResult):
    """Show performance by regime."""
    print(f"\n  📊 {bold('Performance by Regime')}")
    print(f"  {'─' * 60}")

    breakdown = result.performance_by_regime()

    if not breakdown:
        print(f"  {dim('No regime data available')}")
        return

    print(
        f"\n  {'Regime':<10} {'Trades':>8} {'Wins':>6} {'WR%':>8} {'Total P&L':>12} {'Avg P&L':>10}"
    )
    print(f"  {'-' * 56}")

    for regime in ["bull", "bear", "range", "crash"]:
        if regime in breakdown:
            stats = breakdown[regime]
            pnl_color = green if stats["total_pnl"] > 0 else red

            total_pnl_str = f"{stats['total_pnl']:>+11.2f}%"
            avg_pnl_str = f"{stats['avg_pnl']:>+9.2f}%"

            print(
                f"  {regime.upper():<10} {stats['trades']:>8} {stats['wins']:>6} "
                f"{stats['winrate']:>7.1f}% "
                f"{pnl_color(total_pnl_str)} "
                f"{pnl_color(avg_pnl_str)}"
            )


def _show_exit_breakdown(result: BacktestResult):
    """Show performance by exit reason."""
    print(f"\n  📊 {bold('Performance by Exit Reason')}")
    print(f"  {'─' * 60}")

    breakdown = result.performance_by_exit_reason()

    if not breakdown:
        print(f"  {dim('No exit data available')}")
        return

    print(f"\n  {'Reason':<20} {'Trades':>8} {'Wins':>6} {'WR%':>8} {'Avg P&L':>10}")
    print(f"  {'-' * 54}")

    for reason, stats in sorted(breakdown.items(), key=lambda x: -x[1]["trades"]):
        pnl = stats.get("avg_pnl", 0)
        pnl_color = green if pnl > 0 else red

        print(
            f"  {reason:<20} {stats['trades']:>8} {stats['wins']:>6} "
            f"{stats.get('winrate', 0):>7.1f}% "
            f"{pnl_color(f'{pnl:>+9.2f}%')}"
        )


# =============================================================================
# EXPORT RESULTS
# =============================================================================


def _export_results():
    """Export results menu."""
    global _last_result

    clear_screen()
    print_header("EXPORT RESULTS")

    if _last_result is None:
        print(f"\n  {yellow('No backtest result to export.')}")
        input("\n  Press Enter to continue...")
        return

    result = _last_result
    reporter = BacktestReporter(result)

    print(f"\n  Result: {result.symbol} - {result.period_name}")
    print(f"  Trades: {result.total_trades()}")
    print()
    print(f"  {cyan('[1]')} Export CSV (trade list)")
    print(f"  {cyan('[2]')} Export JSON (full data)")
    print(f"  {cyan('[3]')} Export both CSV + JSON")

    if HTML_EXPORT_AVAILABLE:
        print(f"  {cyan('[4]')} Export HTML report (interactive charts)")
        print(f"  {cyan('[5]')} Export ALL formats")

    print()
    choice = input(f"  {bold('Select format')}: ").strip()

    if choice in ("1", "3", "5"):
        path = reporter.save_csv()
        print(f"\n  {green('✓')} CSV: {path}")

    if choice in ("2", "3", "5"):
        path = reporter.save_json()
        print(f"\n  {green('✓')} JSON: {path}")

    if choice in ("4", "5") and HTML_EXPORT_AVAILABLE:
        _export_html(result)

    input("\n  Press Enter to continue...")


def _export_html(result: BacktestResult):
    """Export HTML report."""
    try:
        from signalbolt.backtest.html_reporter import generate_html_report

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = RESULTS_DIR / f"report_{result.symbol}_{timestamp}.html"

        print(f"\n  {cyan('Generating HTML report...')}")
        print(f"  {dim(f'Output: {output_path.absolute()}')}")

        # Upewnij się że folder istnieje
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        # Generuj raport
        path = generate_html_report(
            result,
            str(output_path),
            auto_open=False,  # Zapytamy sami
        )

        # Sprawdź czy plik faktycznie istnieje
        if Path(path).exists():
            file_size = Path(path).stat().st_size / 1024  # KB
            print(f"\n  {green('✓')} HTML report saved: {path}")
            print(f"  {dim(f'File size: {file_size:.1f} KB')}")

            # Zapytaj czy otworzyć
            if input_yes_no("  Open in browser now?", default=True):
                import webbrowser

                webbrowser.open(f"file://{Path(path).absolute()}")
                print(f"  {green('✓')} Opened in browser")
        else:
            print(f"\n  {red('✗')} File was not created at expected path!")
            print(f"  {dim(f'Expected: {output_path.absolute()}')}")

    except ImportError:
        print(f"\n  {yellow('⚠ Plotly not installed. Run: pip install plotly')}")
    except Exception as e:
        print(f"\n  {red(f'HTML export error: {e}')}")
        import traceback

        traceback.print_exc()


# =============================================================================
# PRO: MONTE CARLO
# =============================================================================


def _monte_carlo_menu():
    """Monte Carlo validation."""
    if not PRO_MONTE_CARLO:
        _show_pro_message(
            "Monte Carlo Validation",
            "Test strategy robustness by shuffling trades randomly.",
        )
        return

    global _last_result

    clear_screen()
    print_header("MONTE CARLO SIMULATION")

    if _last_result is None:
        print(f"\n  {yellow('Run a backtest first to get a result.')}")
        input("\n  Press Enter to continue...")
        return

    from signalbolt.backtest.pro.monte_carlo import MonteCarloSimulator

    result = _last_result

    print(f"\n  Result: {result.symbol} - {result.period_name}")
    print(f"  Trades: {result.total_trades()}")

    if result.total_trades() < 10:
        print(f"\n  {yellow('Need at least 10 trades for Monte Carlo.')}")
        input("\n  Press Enter to continue...")
        return

    # Settings
    print(f"\n  {cyan('Settings:')}")
    num_sims = int(
        input_number(
            "  Number of simulations", default=1000, min_val=100, max_val=10000
        )
    )

    print(f"\n  Method:")
    print(f"    {cyan('1')}. Shuffle  {dim('(randomize trade order)')}")
    print(f"    {cyan('2')}. Bootstrap {dim('(resample with replacement)')}")
    print(f"    {cyan('3')}. Subset   {dim('(random 80% of trades)')}")

    method_choice = input(f"\n  Method [1-3]: ").strip()
    method = {"1": "shuffle", "2": "bootstrap", "3": "subset"}.get(
        method_choice, "shuffle"
    )

    print(f"\n  {cyan('Running Monte Carlo...')}\n")

    try:
        simulator = MonteCarloSimulator(verbose=True)
        mc_result = simulator.run(
            result=result,
            num_simulations=num_sims,
            method=method,
        )

        # Summary already printed by simulator

    except Exception as e:
        print(f"\n  {red(f'Error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# PRO: WALK-FORWARD
# =============================================================================


def _walk_forward_menu():
    """Walk-forward analysis."""
    if not PRO_WALK_FORWARD:
        _show_pro_message(
            "Walk-Forward Analysis",
            "Detect overfitting by testing on unseen data segments.",
        )
        return

    clear_screen()
    print_header("WALK-FORWARD ANALYSIS")

    from signalbolt.backtest.pro.walk_forward import WalkForwardAnalyzer

    # Config
    configs = _get_available_configs()
    config_path = _quick_select_config(configs)
    if config_path is None:
        return

    config = BacktestConfig.from_yaml(config_path)

    symbol = input_string("\n  Symbol", default="BTCUSDT").upper()
    start_date = input_string("  Start date", default="2022-01-01")
    end_date = input_string("  End date", default="2023-12-31")
    num_windows = int(
        input_number("  Number of windows", default=5, min_val=3, max_val=10)
    )

    print(f"\n  {cyan('Running Walk-Forward Analysis...')}\n")

    try:
        analyzer = WalkForwardAnalyzer(verbose=True)
        wf_result = analyzer.run(
            config=config,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            num_windows=num_windows,
        )

        # Summary already printed by analyzer

    except Exception as e:
        print(f"\n  {red(f'Error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# PRO: MULTI-SYMBOL
# =============================================================================


def _multi_symbol_menu():
    """Multi-symbol batch testing."""
    if not PRO_MULTI_SYMBOL:
        _show_pro_message(
            "Multi-Symbol Batch Testing", "Test strategy on 10+ symbols simultaneously."
        )
        return

    clear_screen()
    print_header("MULTI-SYMBOL BATCH TEST")

    from signalbolt.backtest.pro.multi_symbol import MultiSymbolTester

    # Config
    configs = _get_available_configs()
    config_path = _quick_select_config(configs)
    if config_path is None:
        return

    config = BacktestConfig.from_yaml(config_path)

    # Symbol selection
    all_symbols = list(SYMBOL_DATA_START.keys())

    print(f"\n  Available symbols:")
    for i, sym in enumerate(all_symbols, 1):
        print(f"    {cyan(str(i)):>4}. {sym}")

    print(f"\n    {cyan('A')}. All symbols")
    print(f"    {cyan('T')}. Top 5 (BTC, ETH, BNB, SOL, XRP)")

    sym_choice = input(f"\n  Selection [A/T/1,2,3...]: ").strip().upper()

    if sym_choice == "A":
        symbols = all_symbols
    elif sym_choice == "T":
        symbols = all_symbols[:5]
    else:
        try:
            indices = [int(x.strip()) - 1 for x in sym_choice.split(",")]
            symbols = [all_symbols[i] for i in indices if 0 <= i < len(all_symbols)]
        except (ValueError, IndexError):
            symbols = all_symbols[:5]

    start_date = input_string("\n  Start date", default="2023-01-01")
    end_date = input_string("  End date", default="2023-12-31")

    print(f"\n  {cyan(f'Testing {len(symbols)} symbols...')}\n")

    try:
        tester = MultiSymbolTester(verbose=True)
        ms_result = tester.run(
            config=config,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        # Summary already printed by tester

    except Exception as e:
        print(f"\n  {red(f'Error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# PRO: COMPARE CONFIGS
# =============================================================================


def _compare_configs_menu():
    """Compare multiple configs side-by-side."""
    if not PRO_MULTI_CONFIG:
        _show_pro_message(
            "Config Comparison", "Compare multiple configs side-by-side on same data."
        )
        return

    clear_screen()
    print_header("COMPARE CONFIGS")

    from signalbolt.backtest.pro.multi_config import MultiConfigTester

    configs = _get_available_configs()

    if len(configs) < 2:
        print(f"\n  {yellow('Need at least 2 config files for comparison.')}")
        input("\n  Press Enter to continue...")
        return

    # Select configs
    print(f"\n  Available configs:")
    for i, cfg in enumerate(configs, 1):
        desc = _get_config_description(cfg)
        print(f"    {cyan(str(i))}. {cfg:<30} {dim(desc)}")

    print(f"\n    {cyan('A')}. All configs")

    cfg_choice = input(f"\n  Select configs [A/1,2,3...]: ").strip().upper()

    if cfg_choice == "A":
        selected_paths = [str(CONFIGS_DIR / c) for c in configs]
    else:
        try:
            indices = [int(x.strip()) - 1 for x in cfg_choice.split(",")]
            selected_paths = [
                str(CONFIGS_DIR / configs[i]) for i in indices if 0 <= i < len(configs)
            ]
        except (ValueError, IndexError):
            selected_paths = [str(CONFIGS_DIR / c) for c in configs[:3]]

    if len(selected_paths) < 2:
        print(f"\n  {yellow('Select at least 2 configs.')}")
        input("\n  Press Enter to continue...")
        return

    symbol = input_string("\n  Symbol", default="BTCUSDT").upper()
    start_date = input_string("  Start date", default="2023-01-01")
    end_date = input_string("  End date", default="2023-12-31")

    print(f"\n  {cyan(f'Comparing {len(selected_paths)} configs...')}\n")

    try:
        tester = MultiConfigTester(verbose=True)
        mc_result = tester.run(
            config_paths=selected_paths,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        # Summary already printed by tester

        # Offer HTML export
        if HTML_EXPORT_AVAILABLE:
            if input_yes_no("\n  Generate HTML comparison report?", default=True):
                try:
                    from signalbolt.backtest.html_reporter import (
                        generate_comparison_report,
                    )

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output_path = RESULTS_DIR / f"comparison_{symbol}_{timestamp}.html"
                    path = generate_comparison_report(mc_result, str(output_path))
                    print(f"\n  {green('✓')} HTML: {path}")
                except Exception as e:
                    print(f"\n  {red(f'HTML error: {e}')}")

        # Offer JSON export
        if input_yes_no("  Save results to JSON?", default=False):
            path = mc_result.save_json(RESULTS_DIR)
            print(f"\n  {green('✓')} JSON: {path}")

    except Exception as e:
        print(f"\n  {red(f'Error: {e}')}")
        import traceback

        traceback.print_exc()

    input(f"\n  Press Enter to continue...")


# =============================================================================
# PRO LOCK MESSAGE
# =============================================================================


def _show_pro_message(feature_name: str, description: str):
    """Show PRO feature locked message."""
    clear_screen()

    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  🔒 PRO FEATURE: {feature_name:<50} ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  {description:<68} ║
║                                                                      ║
║  This advanced feature is available in SignalBolt PRO.               ║
║                                                                      ║
║  PRO includes:                                                       ║
║    • Monte Carlo Validation    (strategy robustness)                 ║
║    • Walk-Forward Analysis     (overfitting detection)               ║
║    • Multi-Symbol Batch Test   (10+ symbols at once)                 ║
║    • Config Comparison         (side-by-side ranking)                ║
║    • HTML Reports              (interactive Plotly charts)           ║
║    • Benchmark Comparison      (vs Buy & Hold)                       ║
║    • Priority Support                                                ║
║                                                                      ║
║  📧 Contact: signalbolt@proton.me                                    ║
║  💬 Discord: discord.gg/JWeKseJsmE                                   ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
    """)

    input("\n  Press Enter to go back...")


# =============================================================================
# SHARED HELPERS
# =============================================================================


def _get_available_configs() -> List[str]:
    """Get list of available config files."""
    configs = []
    if CONFIGS_DIR.exists():
        for f in sorted(CONFIGS_DIR.glob("*.yaml")):
            if not f.name.startswith("_"):
                configs.append(f.name)
    return configs


def _get_config_description(config_name: str) -> str:
    """Get short description from config name."""
    name_lower = config_name.lower()
    if "safe" in name_lower or "conservative" in name_lower:
        return "Conservative settings"
    elif "aggressive" in name_lower:
        return "Aggressive settings"
    elif "balanced" in name_lower:
        return "Balanced settings"
    elif "default" in name_lower:
        return "Default settings"
    return ""


def _quick_select_config(configs: List[str]) -> Optional[str]:
    """Quick config selection, returns full path or None."""
    if not configs:
        print(f"\n  {red('No config files found.')}")
        return None

    print(f"\n  Select config:")
    show = configs[:5]
    for i, cfg in enumerate(show, 1):
        print(f"    {i}. {cfg}")

    cfg_choice = input(f"\n  Config [1-{len(show)}]: ").strip()

    try:
        config_idx = int(cfg_choice) - 1
        if 0 <= config_idx < len(show):
            return str(CONFIGS_DIR / show[config_idx])
    except (ValueError, IndexError):
        pass

    return str(CONFIGS_DIR / configs[0])


def _select_symbol() -> Optional[str]:
    """Interactive symbol selection."""
    print(f"\n  {cyan('STEP 2:')} {bold('Select Symbol')}")
    print(f"  {'─' * 60}")

    symbols = list(SYMBOL_DATA_START.keys())[:5]

    print()
    for i, sym in enumerate(symbols, 1):
        earliest = SYMBOL_DATA_START.get(sym, "2020-01-01")
        print(f"    {cyan(str(i))}. {sym:<12} {dim(f'(data from {earliest})')}")

    print(f"\n    {cyan('C')}. Custom symbol")
    print()

    sym_choice = input(f"  Symbol [1-5/C]: ").strip().upper()

    if sym_choice == "C":
        return input_string("  Enter symbol (e.g., AVAXUSDT)").upper()
    elif sym_choice.isdigit() and 1 <= int(sym_choice) <= 5:
        return symbols[int(sym_choice) - 1]
    else:
        return "BTCUSDT"


def _select_period(earliest: str, latest: str):
    """Interactive period selection. Returns (start, end, name) or (None, None, None)."""
    print(f"\n  {cyan('STEP 3:')} {bold('Select Period')}")
    print(f"  {'─' * 60}")

    today = datetime.now()

    periods = [
        ("1", "Last Month", (today - timedelta(days=30)).strftime("%Y-%m-%d"), latest),
        (
            "2",
            "Last 3 Months",
            (today - timedelta(days=90)).strftime("%Y-%m-%d"),
            latest,
        ),
        (
            "3",
            "Last 6 Months",
            (today - timedelta(days=180)).strftime("%Y-%m-%d"),
            latest,
        ),
        ("4", "Last Year", (today - timedelta(days=365)).strftime("%Y-%m-%d"), latest),
        ("5", "Year to Date", f"{today.year}-01-01", latest),
        ("6", "Custom dates", None, None),
    ]

    print()
    for code, name, start, end in periods:
        if start and end:
            days = (
                datetime.strptime(end, "%Y-%m-%d")
                - datetime.strptime(start, "%Y-%m-%d")
            ).days
            print(f"    {cyan(code)}. {name:<20} {dim(f'{start} → {end}')} ({days}d)")
        else:
            print(f"    {cyan(code)}. {name:<20} {dim('Enter your own dates')}")

    print()
    period_choice = input(f"  Period [1-6]: ").strip()

    if period_choice == "6":
        print(f"\n  {dim(f'Available range: {earliest} to {latest}')}")

        while True:
            start_date = input_string("  Start date (YYYY-MM-DD)", default=earliest)
            try:
                datetime.strptime(start_date, "%Y-%m-%d")
                break
            except ValueError:
                print(f"  {red('Invalid format')}")

        while True:
            end_date = input_string("  End date (YYYY-MM-DD)", default=latest)
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                if end_dt <= start_dt:
                    print(f"  {red('End must be after start')}")
                    continue
                break
            except ValueError:
                print(f"  {red('Invalid format')}")

        period_name = f"Custom ({start_date} to {end_date})"
    else:
        try:
            idx = int(period_choice) - 1
            if 0 <= idx < len(periods) - 1:
                _, period_name, start_date, end_date = periods[idx]
            else:
                _, period_name, start_date, end_date = periods[1]
        except (ValueError, IndexError):
            _, period_name, start_date, end_date = periods[1]

    # Adjust if before earliest
    earliest_dt = datetime.strptime(earliest, "%Y-%m-%d")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    if start_dt < earliest_dt:
        start_date = earliest
        print(f"\n  {yellow('⚠')} Adjusted start to {earliest} (earliest available)")

    return start_date, end_date, period_name


def _save_result(result: BacktestResult):
    """Save result in multiple formats (CSV + JSON only)."""
    reporter = BacktestReporter(result)

    csv_path = reporter.save_csv()
    json_path = reporter.save_json()

    print(f"\n  {green('✓')} CSV:  {csv_path}")
    print(f"  {green('✓')} JSON: {json_path}")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    run_backtest_menu()
