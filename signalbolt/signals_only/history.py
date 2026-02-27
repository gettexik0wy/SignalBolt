"""
Signal history management and analysis.

Provides:
- Signal retrieval with filters
- Performance tracking (if outcomes recorded)
- Export functionality
- Statistics
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pathlib import Path
import json

from signalbolt.signals_only.session import SignalsSession, StoredSignal
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.signals_only.history")


class SignalHistory:
    """
    Signal history manager.

    Provides advanced querying and analysis of stored signals.
    """

    def __init__(self, session: SignalsSession):
        """Initialize with session."""
        self.session = session

    # =========================================================================
    # QUERIES
    # =========================================================================

    def get_by_symbol(self, symbol: str, limit: int = 100) -> List[StoredSignal]:
        """Get signals for specific symbol."""
        return self.session.get_signals(symbol=symbol, limit=limit)

    def get_by_direction(self, direction: str) -> List[StoredSignal]:
        """Get signals by direction (LONG/SHORT)."""
        return [s for s in self.session._signals if s.direction == direction.upper()]

    def get_by_regime(self, regime: str) -> List[StoredSignal]:
        """Get signals by market regime."""
        return [s for s in self.session._signals if s.regime.lower() == regime.lower()]

    def get_by_score_range(
        self, min_score: float = 0, max_score: float = 100
    ) -> List[StoredSignal]:
        """Get signals within score range."""
        return [s for s in self.session._signals if min_score <= s.score <= max_score]

    def get_by_date_range(
        self, start_date: datetime, end_date: Optional[datetime] = None
    ) -> List[StoredSignal]:
        """Get signals within date range."""
        end_date = end_date or datetime.now()

        return [
            s for s in self.session._signals if start_date <= s.timestamp <= end_date
        ]

    def get_last_n(self, n: int = 10) -> List[StoredSignal]:
        """Get last N signals."""
        return self.session._signals[-n:]

    def get_high_quality(self, min_score: float = 80) -> List[StoredSignal]:
        """Get high quality signals only."""
        return self.get_by_score_range(min_score=min_score)

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_symbol_stats(self) -> Dict[str, dict]:
        """Get statistics per symbol."""
        stats = {}

        for signal in self.session._signals:
            symbol = signal.symbol

            if symbol not in stats:
                stats[symbol] = {
                    "count": 0,
                    "long": 0,
                    "short": 0,
                    "avg_score": 0.0,
                    "total_score": 0.0,
                }

            stats[symbol]["count"] += 1
            stats[symbol]["total_score"] += signal.score

            if signal.direction == "LONG":
                stats[symbol]["long"] += 1
            else:
                stats[symbol]["short"] += 1

        # Calculate averages
        for symbol in stats:
            if stats[symbol]["count"] > 0:
                stats[symbol]["avg_score"] = (
                    stats[symbol]["total_score"] / stats[symbol]["count"]
                )
            del stats[symbol]["total_score"]

        return stats

    def get_hourly_distribution(self) -> Dict[int, int]:
        """Get signal count by hour of day."""
        distribution = {h: 0 for h in range(24)}

        for signal in self.session._signals:
            hour = signal.timestamp.hour
            distribution[hour] += 1

        return distribution

    def get_daily_distribution(self) -> Dict[str, int]:
        """Get signal count by day of week."""
        days = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        distribution = {day: 0 for day in days}

        for signal in self.session._signals:
            day = days[signal.timestamp.weekday()]
            distribution[day] += 1

        return distribution

    def get_regime_stats(self) -> Dict[str, dict]:
        """Get statistics per regime."""
        stats = {}

        for signal in self.session._signals:
            regime = signal.regime

            if regime not in stats:
                stats[regime] = {
                    "count": 0,
                    "avg_score": 0.0,
                    "total_score": 0.0,
                }

            stats[regime]["count"] += 1
            stats[regime]["total_score"] += signal.score

        for regime in stats:
            if stats[regime]["count"] > 0:
                stats[regime]["avg_score"] = (
                    stats[regime]["total_score"] / stats[regime]["count"]
                )
            del stats[regime]["total_score"]

        return stats

    # =========================================================================
    # OUTCOME TRACKING
    # =========================================================================

    def record_outcome(
        self,
        signal_index: int,
        outcome: str,
        pnl_pct: Optional[float] = None,
        notes: str = "",
    ):
        """
        Record actual outcome for a signal.

        Args:
            signal_index: Index of signal in history
            outcome: "hit_tp", "hit_sl", "timeout", "manual"
            pnl_pct: Actual P&L percentage
            notes: Additional notes
        """
        if 0 <= signal_index < len(self.session._signals):
            signal = self.session._signals[signal_index]
            signal.actual_outcome = outcome
            signal.actual_pnl_pct = pnl_pct
            signal.user_notes = notes

            log.info(f"Recorded outcome for {signal.symbol}: {outcome} ({pnl_pct}%)")

    def get_outcome_stats(self) -> dict:
        """Get statistics for signals with recorded outcomes."""
        signals_with_outcome = [
            s for s in self.session._signals if s.actual_outcome is not None
        ]

        if not signals_with_outcome:
            return {"message": "No outcomes recorded yet"}

        total = len(signals_with_outcome)
        wins = len(
            [
                s
                for s in signals_with_outcome
                if s.actual_pnl_pct and s.actual_pnl_pct > 0
            ]
        )
        losses = len(
            [
                s
                for s in signals_with_outcome
                if s.actual_pnl_pct and s.actual_pnl_pct < 0
            ]
        )

        total_pnl = sum(s.actual_pnl_pct or 0 for s in signals_with_outcome)
        avg_pnl = total_pnl / total if total > 0 else 0

        # By outcome type
        outcomes = {}
        for s in signals_with_outcome:
            outcomes[s.actual_outcome] = outcomes.get(s.actual_outcome, 0) + 1

        return {
            "total_tracked": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "total_pnl_pct": total_pnl,
            "avg_pnl_pct": avg_pnl,
            "by_outcome": outcomes,
        }

    # =========================================================================
    # EXPORT
    # =========================================================================

    def export_filtered(
        self, signals: List[StoredSignal], output_file: str, format: str = "csv"
    ) -> str:
        """Export filtered signals."""
        output_path = Path(output_file)

        if format == "csv":
            import csv

            with open(output_path, "w", newline="") as f:
                if signals:
                    writer = csv.DictWriter(f, fieldnames=signals[0].to_dict().keys())
                    writer.writeheader()
                    for signal in signals:
                        writer.writerow(signal.to_dict())

        elif format == "json":
            with open(output_path, "w") as f:
                json.dump([s.to_dict() for s in signals], f, indent=2)

        log.info(f"Exported {len(signals)} signals to {output_path}")

        return str(output_path)
