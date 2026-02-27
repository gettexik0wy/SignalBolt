"""
Coin Details Analyzer with Colorama colors.
"""

from typing import Dict
from datetime import datetime

from signalbolt.core.config import get_config
from signalbolt.core.indicators import IndicatorCalculator
from signalbolt.core.scoring import calculate_score
from signalbolt.exchange.client import get_exchange
from signalbolt.data.manager import DataManager
from signalbolt.cli.utils import (
    clear_screen, print_header, print_divider,
    input_string, input_yes_no,
    green, red, yellow, cyan, bold, dim,
    bold_green, bold_red, bold_cyan,
    format_usd, format_pct_colored, format_crypto_price
)
from signalbolt.utils.logger import get_logger

log = get_logger('signalbolt.cli.coin_details')

TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d']
TIMEFRAME_NAMES = {
    '1m': '1 Minute',
    '5m': '5 Minutes',
    '15m': '15 Minutes',
    '1h': '1 Hour',
    '4h': '4 Hours',
    '1d': '1 Day',
}


class CoinAnalyzer:

    def __init__(self):
        self.exchange = get_exchange()
        self.data_manager = DataManager(mode='auto', exchange=self.exchange)
        self.config = get_config()
        self.indicator_calc = IndicatorCalculator(
            enable_macd=True,
            enable_bb=True,
            enable_stoch=True
        )

    def analyze(self, symbol: str) -> Dict:
        result = {
            'symbol': symbol,
            'timestamp': datetime.now(),
            'price_info': None,
            'timeframes': {},
            'errors': []
        }

        try:
            result['price_info'] = self._get_price_info(symbol)
        except Exception as e:
            result['errors'].append(str(e))

        for tf in TIMEFRAMES:
            try:
                result['timeframes'][tf] = self._analyze_timeframe(symbol, tf)
            except Exception as e:
                result['timeframes'][tf] = {'error': str(e)}
                result['errors'].append(f"{tf}: {e}")

        return result

    def _get_price_info(self, symbol: str) -> Dict:
        ticker = self.exchange.get_ticker(symbol)
        if not ticker:
            raise Exception("Failed to get ticker")

        daily_df = self.data_manager.get_candles(symbol, '1d', limit=31)
        change_7d = 0.0
        change_30d = 0.0

        if daily_df is not None and len(daily_df) >= 7:
            price_now = daily_df['close'].iloc[-1]
            price_7d = daily_df['close'].iloc[-7]
            change_7d = ((price_now - price_7d) / price_7d) * 100
            if len(daily_df) >= 30:
                price_30d = daily_df['close'].iloc[-30]
                change_30d = ((price_now - price_30d) / price_30d) * 100

        return {
            'price': ticker.last,
            'bid': ticker.bid,
            'ask': ticker.ask,
            'spread_pct': ticker.spread_pct,
            'volume_24h': ticker.volume_24h,
            'change_24h': ticker.change_24h_pct,
            'change_7d': change_7d,
            'change_30d': change_30d,
            'high_24h': ticker.high_24h,
            'low_24h': ticker.low_24h,
        }

    def _analyze_timeframe(self, symbol: str, timeframe: str) -> Dict:
        df = self.data_manager.get_candles(symbol, timeframe, limit=100)
        if df is None or len(df) < 50:
            raise Exception("Insufficient data")

        df_calc = self.indicator_calc.calculate(df, symbol)
        ind = self.indicator_calc.get_latest(df_calc)
        score = calculate_score(ind, 'LONG', enable_bonus=True)

        ema_long = ind.ema9 > ind.ema21 > ind.ema50
        ema_short = ind.ema9 < ind.ema21 < ind.ema50
        min_score = self.config.get('scanner', 'min_signal_score', default=70)

        signal = None
        if ema_long and score.total >= min_score:
            signal = 'LONG'
        elif ema_short and score.total >= min_score:
            signal = 'SHORT'

        alignment = 'LONG' if ema_long else ('SHORT' if ema_short else 'NONE')

        return {
            'indicators': ind,
            'score': score,
            'ema_alignment': alignment,
            'signal': signal,
            'min_score': min_score,
        }

    def print_analysis(self, result: Dict):
        sym = result['symbol']
        ts = result['timestamp'].strftime('%Y-%m-%d %H:%M:%S')

        # Header
        print("")
        print(cyan("═" * 70))
        print(cyan("║") + f" 📊 {bold(sym)} Analysis")
        print(cyan("║") + f" {dim(ts)}")
        print(cyan("═" * 70))

        # Price info
        pi = result.get('price_info')
        if pi:
            self._print_price_info(pi)

        # Timeframes
        for tf in TIMEFRAMES:
            td = result['timeframes'].get(tf, {})
            if 'error' in td:
                print("")
                print(red(f"[{tf}] ERROR: {td['error']}"))
            else:
                self._print_timeframe(tf, td)

        # Summary
        self._print_summary(result)

        print("")
        print(cyan("═" * 70))

    def _print_price_info(self, pi: Dict):
        print("")
        print(cyan("─" * 70))
        print(cyan("║") + f" 💰 {bold('PRICE INFORMATION')}")
        print(cyan("─" * 70))

        price_str = format_crypto_price(pi['price'])
        bid_str = format_crypto_price(pi['bid'])
        ask_str = format_crypto_price(pi['ask'])
        spread_str = f"{pi['spread_pct']:.4f}%"
        high_str = format_crypto_price(pi['high_24h'])
        low_str = format_crypto_price(pi['low_24h'])
        vol_str = format_usd(pi['volume_24h'])

        print(f"  {bold('Current Price:')}  {bold_green(price_str)}")
        print(f"  {bold('Bid / Ask:')}      {bid_str} / {ask_str}")
        print(f"  {bold('Spread:')}         {yellow(spread_str)}")
        print(f"  {bold('24h High/Low:')}   {green(high_str)} / {red(low_str)}")
        print(f"  {bold('24h Volume:')}     {cyan(vol_str)}")
        print("")
        print(f"  {bold('Changes:')}")
        print(f"    24h: {format_pct_colored(pi['change_24h'])}")
        print(f"    7d:  {format_pct_colored(pi['change_7d'])}")
        print(f"    30d: {format_pct_colored(pi['change_30d'])}")

    def _print_timeframe(self, tf: str, td: Dict):
        ind = td['indicators']
        sc = td['score']
        sig = td.get('signal')
        align = td.get('ema_alignment', 'NONE')
        min_sc = td.get('min_score', 70)

        # Signal display
        if sig == 'LONG':
            sig_str = bold_green("✅ LONG SIGNAL")
        elif sig == 'SHORT':
            sig_str = bold_red("📉 SHORT SIGNAL")
        else:
            sig_str = dim("No signal")

        print("")
        print(cyan("─" * 70))
        print(cyan("║") + f" ⏱️  {bold(TIMEFRAME_NAMES[tf])} ({tf})  |  {sig_str}")
        print(cyan("─" * 70))

        # EMAs
        print(f"  {bold('EMAs:')}")
        print(f"    EMA9:  {cyan(format_crypto_price(ind.ema9))}")
        print(f"    EMA21: {cyan(format_crypto_price(ind.ema21))}")
        print(f"    EMA50: {cyan(format_crypto_price(ind.ema50))}")

        if align == 'LONG':
            align_str = green("BULLISH ▲")
        elif align == 'SHORT':
            align_str = red("BEARISH ▼")
        else:
            align_str = yellow("NEUTRAL ─")
        print(f"    Alignment: {align_str}")

        # Momentum
        print(f"  {bold('Momentum:')}")

        # RSI
        rsi = ind.rsi
        if rsi >= 70:
            rsi_str = f"{rsi:.1f} " + red("(Overbought)")
        elif rsi <= 30:
            rsi_str = f"{rsi:.1f} " + green("(Oversold)")
        else:
            rsi_str = f"{rsi:.1f}"
        print(f"    RSI: {rsi_str}")

        # ADX
        adx = ind.adx
        if adx >= 25:
            adx_str = f"{adx:.1f} " + green("(Trending)")
        else:
            adx_str = f"{adx:.1f} " + yellow("(Weak)")
        print(f"    ADX: {adx_str}")

        print(f"    +DI: {ind.di_plus:.1f}  |  -DI: {ind.di_minus:.1f}")

        atr_pct_str = f"{ind.atr_pct:.2f}%"
        print(f"    ATR: {ind.atr:.6f} ({yellow(atr_pct_str)})")

        # Volume
        print(f"  {bold('Volume:')}")
        vol_ratio = ind.volume_ratio
        if vol_ratio >= 1.5:
            vol_str = f"{vol_ratio:.2f}x avg " + green("(High)")
        elif vol_ratio >= 1.0:
            vol_str = f"{vol_ratio:.2f}x avg"
        else:
            vol_str = f"{vol_ratio:.2f}x avg " + yellow("(Low)")
        print(f"    Ratio: {vol_str}")

        # Bonus indicators
        if ind.macd is not None:
            print(f"  {bold('Bonus Indicators:')}")
            macd_sig = ind.macd_signal if ind.macd_signal else 0.0
            macd_hist = ind.macd_histogram if ind.macd_histogram else 0.0
            print(f"    MACD: {ind.macd:.6f}  |  Signal: {macd_sig:.6f}  |  Hist: {macd_hist:.6f}")

            if ind.bb_upper:
                bb_mid = ind.bb_middle if ind.bb_middle else 0.0
                bb_low = ind.bb_lower if ind.bb_lower else 0.0
                print(f"    BB: Upper {ind.bb_upper:,.2f}  |  Mid {bb_mid:,.2f}  |  Lower {bb_low:,.2f}")

            if ind.stoch_k is not None:
                stoch_d = ind.stoch_d if ind.stoch_d else 0.0
                print(f"    Stochastic: %K {ind.stoch_k:.1f}  |  %D {stoch_d:.1f}")

        # Score
        print("")
        score_str = f"{sc.total:.0f}"
        print(f"  🎯 {bold('SignalBolt Score:')} {bold_cyan(score_str)}/120")
        print(f"     {cyan('├─')} Core: {yellow(f'{sc.core_total:.0f}')}/100")
        print(f"     {cyan('│')}   EMA={sc.ema_alignment:.0f} ADX={sc.adx_strength:.0f} "
              f"RSI={sc.rsi:.0f} Vol={sc.volume:.0f} "
              f"Price={sc.price_position:.0f} DI={sc.di_spread:.0f}")
        print(f"     {cyan('└─')} Bonus: {yellow(f'{sc.bonus_total:.0f}')}/20")
        print(f"         MACD={sc.macd_bonus:.0f} BB={sc.bb_bonus:.0f} Stoch={sc.stoch_bonus:.0f}")

        # Threshold check
        if sc.total >= min_sc:
            if align != 'NONE':
                print(f"  {green('✅')} Score meets threshold ({sc.total:.0f} >= {min_sc})")
            else:
                print(f"  {yellow('⚠️')} Score OK but no EMA alignment")
        else:
            print(f"  {red('❌')} Score below threshold ({sc.total:.0f} < {min_sc})")

    def _print_summary(self, result: Dict):
        print("")
        print(cyan("─" * 70))
        print(cyan("║") + f" 📋 {bold('SUMMARY')}")
        print(cyan("─" * 70))

        signals_found = []
        all_scores = []

        for tf in TIMEFRAMES:
            td = result['timeframes'].get(tf, {})
            if 'error' in td:
                continue
            sig = td.get('signal')
            if sig:
                signals_found.append((tf, sig))
            all_scores.append((tf, td['score'].total))

        # Best score
        if all_scores:
            best_tf, best_sc = max(all_scores, key=lambda x: x[1])
            best_str = f"{best_sc:.0f}"
            print(f"  {bold('Best Score:')} {bold_green(best_str)} on {cyan(TIMEFRAME_NAMES[best_tf])}")

        # Signals
        if signals_found:
            print(f"  {bold('Signals Found:')}")
            for tf, sig in signals_found:
                if sig == 'LONG':
                    sig_colored = green(sig)
                else:
                    sig_colored = red(sig)
                print(f"    • {TIMEFRAME_NAMES[tf]}: {sig_colored}")
        else:
            print(f"  {yellow('No signals on any timeframe')}")

        # Errors
        if result['errors']:
            print(f"  {bold('Errors:')}")
            for err in result['errors']:
                print(f"    • {red(err)}")


def run_coin_details():
    analyzer = CoinAnalyzer()

    while True:
        clear_screen()
        print_header("COIN DETAILS ANALYZER")
        print("\n  Analyze any coin across all timeframes.")
        print("  Get indicators, scores, and signal status.\n")

        symbol = input_string("Enter symbol:", default="BTCUSDT").upper()
        if not symbol.endswith('USDT'):
            symbol = symbol + 'USDT'

        print(f"\n🔍 Analyzing {bold(symbol)}...")
        print("   This may take a moment...\n")

        try:
            result = analyzer.analyze(symbol)
            analyzer.print_analysis(result)
        except Exception as e:
            print(red(f"\n❌ Error analyzing {symbol}: {e}"))

        if input_yes_no("\nExport to JSON?", default=False):
            try:
                _export_analysis(result)
            except Exception as e:
                print(red(f"Export error: {e}"))

        if not input_yes_no("\nAnalyze another coin?", default=True):
            break


def _export_analysis(result: Dict):
    import json
    from pathlib import Path

    export = {
        'symbol': result['symbol'],
        'timestamp': result['timestamp'].isoformat(),
        'price_info': result['price_info'],
        'timeframes': {}
    }

    for tf, data in result['timeframes'].items():
        if 'error' in data:
            export['timeframes'][tf] = {'error': data['error']}
        else:
            export['timeframes'][tf] = {
                'indicators': data['indicators'].to_dict(),
                'score': data['score'].to_dict(),
                'ema_alignment': data['ema_alignment'],
                'signal': data['signal'],
            }

    out_dir = Path('data/coin_analysis')
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{result['symbol']}_{result['timestamp'].strftime('%Y%m%d_%H%M%S')}.json"
    fpath = out_dir / fname

    with open(fpath, 'w') as f:
        json.dump(export, f, indent=2)

    print(green(f"\n✅ Exported to: {fpath}"))


if __name__ == '__main__':
    run_coin_details()