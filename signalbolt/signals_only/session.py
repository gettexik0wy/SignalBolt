"""
Signals-only session management.

Stores signal history without trading.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from dataclasses import dataclass, field

from signalbolt.core.strategy import Signal
from signalbolt.core.config import Config
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.signals_only.session")


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data" / "signal_sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# STORED SIGNAL
# =============================================================================


@dataclass
class StoredSignal:
    """Stored signal with metadata."""

    # Signal data
    symbol: str
    direction: str
    timestamp: datetime
    score: float
    price: float

    # Metadata
    strategy_name: str
    regime: str
    confidence: str
    notes: str

    # Outcome tracking (optional - user can update)
    actual_outcome: Optional[str] = None  # "hit_tp", "hit_sl", "timeout"
    actual_pnl_pct: Optional[float] = None
    user_notes: str = ""

    # Alert tracking
    alert_sent: bool = False
    alert_sent_at: Optional[datetime] = None

    @classmethod
    def from_signal(cls, signal: Signal) -> "StoredSignal":
        """Create from Signal."""
        return cls(
            symbol=signal.symbol,
            direction=signal.direction,
            timestamp=signal.timestamp,
            score=signal.score,
            price=signal.price,
            strategy_name=signal.strategy_name,
            regime=signal.regime,
            confidence=signal.confidence,
            notes=signal.notes,
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "timestamp": self.timestamp.isoformat(),
            "score": self.score,
            "price": self.price,
            "strategy_name": self.strategy_name,
            "regime": self.regime,
            "confidence": self.confidence,
            "notes": self.notes,
            "actual_outcome": self.actual_outcome,
            "actual_pnl_pct": self.actual_pnl_pct,
            "user_notes": self.user_notes,
            "alert_sent": self.alert_sent,
            "alert_sent_at": self.alert_sent_at.isoformat()
            if self.alert_sent_at
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StoredSignal":
        data = dict(data)
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        if data.get("alert_sent_at"):
            data["alert_sent_at"] = datetime.fromisoformat(data["alert_sent_at"])
        return cls(**data)


# =============================================================================
# SESSION METADATA
# =============================================================================


@dataclass
class SessionMetadata:
    """Signals session metadata."""

    session_id: str
    name: str
    created_at: datetime
    config_name: str
    strategy_name: str

    # Stats
    total_scans: int = 0
    total_signals: int = 0
    alerts_sent: int = 0
    last_active: Optional[datetime] = None

    # Settings
    telegram_enabled: bool = False
    discord_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "config_name": self.config_name,
            "strategy_name": self.strategy_name,
            "total_scans": self.total_scans,
            "total_signals": self.total_signals,
            "alerts_sent": self.alerts_sent,
            "last_active": self.last_active.isoformat() if self.last_active else None,
            "telegram_enabled": self.telegram_enabled,
            "discord_enabled": self.discord_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetadata":
        data = dict(data)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("last_active"):
            data["last_active"] = datetime.fromisoformat(data["last_active"])
        return cls(**data)


# =============================================================================
# SIGNALS SESSION
# =============================================================================


class SignalsSession:
    """
    Session for signals-only mode.

    Stores signal history and metadata.
    """

    def __init__(self, session_dir: Path, metadata: SessionMetadata):
        """Initialize session."""
        self.session_dir = session_dir
        self.metadata = metadata

        # In-memory signal cache
        self._signals: List[StoredSignal] = []

        # Load existing signals
        self._load_signals()

    # =========================================================================
    # FACTORY METHODS
    # =========================================================================

    @classmethod
    def create(
        cls, name: str, config: Config, strategy_name: str = "SignalBoltOriginal"
    ) -> "SignalsSession":
        """Create new signals session."""

        session_id = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session_dir = DATA_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        metadata = SessionMetadata(
            session_id=session_id,
            name=name,
            created_at=datetime.now(),
            config_name=config.mode if hasattr(config, "mode") else "signals",
            strategy_name=strategy_name,
            telegram_enabled=config.get("alerts", "telegram_enabled", default=False),
            discord_enabled=config.get("alerts", "discord_enabled", default=False),
        )

        session = cls(session_dir, metadata)
        session._save_metadata()

        log.info(f"Created signals session: {session_id}")

        return session

    @classmethod
    def load(cls, session_id: str) -> "SignalsSession":
        """Load existing session."""

        session_dir = DATA_DIR / session_id

        if not session_dir.exists():
            # Try to find by name prefix
            matching = list(DATA_DIR.glob(f"{session_id}*"))
            if matching:
                session_dir = matching[-1]
            else:
                raise FileNotFoundError(f"Session not found: {session_id}")

        # Load metadata
        metadata_file = session_dir / "metadata.json"
        with open(metadata_file, "r") as f:
            metadata = SessionMetadata.from_dict(json.load(f))

        session = cls(session_dir, metadata)

        log.info(f"Loaded signals session: {metadata.session_id}")

        return session

    @classmethod
    def list_sessions(cls) -> List[Dict]:
        """List all sessions."""
        sessions = []

        for session_dir in DATA_DIR.iterdir():
            if session_dir.is_dir():
                metadata_file = session_dir / "metadata.json"

                if metadata_file.exists():
                    try:
                        with open(metadata_file, "r") as f:
                            data = json.load(f)

                        sessions.append(
                            {
                                "session_id": data["session_id"],
                                "name": data["name"],
                                "created_at": data["created_at"],
                                "total_signals": data["total_signals"],
                                "last_active": data.get("last_active"),
                            }
                        )
                    except Exception:
                        pass

        sessions.sort(key=lambda x: x["created_at"], reverse=True)

        return sessions

    # =========================================================================
    # SIGNAL STORAGE
    # =========================================================================

    def add_signal(self, signal: Signal) -> StoredSignal:
        """Add signal to session."""

        stored = StoredSignal.from_signal(signal)
        self._signals.append(stored)

        # Append to file
        self._append_signal(stored)

        # Update metadata
        self.metadata.total_signals += 1
        self.metadata.last_active = datetime.now()
        self._save_metadata()

        log.signal(
            f"Stored signal: {signal.symbol} {signal.direction} @ {signal.price:.8f}"
        )

        return stored

    def mark_alert_sent(self, signal: StoredSignal):
        """Mark signal as alert sent."""
        signal.alert_sent = True
        signal.alert_sent_at = datetime.now()
        self.metadata.alerts_sent += 1
        self._save_metadata()

    def update_scan_count(self):
        """Increment scan counter."""
        self.metadata.total_scans += 1
        self.metadata.last_active = datetime.now()
        self._save_metadata()

    def get_signals(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[StoredSignal]:
        """Get stored signals."""

        signals = self._signals

        if symbol:
            signals = [s for s in signals if s.symbol == symbol]

        return signals[-limit:]

    def get_recent_signals(self, hours: int = 24) -> List[StoredSignal]:
        """Get signals from last N hours."""

        cutoff = datetime.now() - timedelta(hours=hours)

        return [s for s in self._signals if s.timestamp >= cutoff]

    def get_today_signals(self) -> List[StoredSignal]:
        """Get today's signals."""
        today = datetime.now().date()
        return [s for s in self._signals if s.timestamp.date() == today]

    # =========================================================================
    # PERSISTENCE
    # =========================================================================

    def _save_metadata(self):
        """Save metadata."""
        metadata_file = self.session_dir / "metadata.json"

        with open(metadata_file, "w") as f:
            json.dump(self.metadata.to_dict(), f, indent=2)

    def _append_signal(self, signal: StoredSignal):
        """Append signal to file."""
        signals_file = self.session_dir / "signals.jsonl"

        with open(signals_file, "a") as f:
            f.write(json.dumps(signal.to_dict()) + "\n")

    def _load_signals(self):
        """Load signals from file."""
        signals_file = self.session_dir / "signals.jsonl"

        if not signals_file.exists():
            return

        with open(signals_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        signal = StoredSignal.from_dict(json.loads(line))
                        self._signals.append(signal)
                    except Exception as e:
                        log.warning(f"Failed to load signal: {e}")

        log.debug(f"Loaded {len(self._signals)} signals from file")

    def export_csv(self, output_file: Optional[str] = None) -> str:
        """Export signals to CSV."""
        import csv

        if output_file is None:
            output_file = (
                self.session_dir / f"signals_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )

        if not self._signals:
            log.warning("No signals to export")
            return str(output_file)

        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._signals[0].to_dict().keys())
            writer.writeheader()

            for signal in self._signals:
                writer.writerow(signal.to_dict())

        log.info(f"Exported {len(self._signals)} signals to {output_file}")

        return str(output_file)

    def delete(self, confirm: bool = False):
        """Delete session."""
        if not confirm:
            raise ValueError("Set confirm=True to delete")

        import shutil

        shutil.rmtree(self.session_dir)
        log.warning(f"Deleted session: {self.metadata.session_id}")

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_stats(self) -> dict:
        """Get session statistics."""
        today_signals = self.get_today_signals()
        recent_signals = self.get_recent_signals(24)

        # Count by direction
        long_count = sum(1 for s in self._signals if s.direction == "LONG")
        short_count = sum(1 for s in self._signals if s.direction == "SHORT")

        # Count by regime
        regime_counts = {}
        for s in self._signals:
            regime_counts[s.regime] = regime_counts.get(s.regime, 0) + 1

        # Top symbols
        symbol_counts = {}
        for s in self._signals:
            symbol_counts[s.symbol] = symbol_counts.get(s.symbol, 0) + 1

        top_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[
            :5
        ]

        return {
            "session_id": self.metadata.session_id,
            "name": self.metadata.name,
            "total_scans": self.metadata.total_scans,
            "total_signals": self.metadata.total_signals,
            "alerts_sent": self.metadata.alerts_sent,
            "today_signals": len(today_signals),
            "last_24h_signals": len(recent_signals),
            "long_signals": long_count,
            "short_signals": short_count,
            "by_regime": regime_counts,
            "top_symbols": top_symbols,
            "last_active": self.metadata.last_active.isoformat()
            if self.metadata.last_active
            else None,
        }

    # =========================================================================
    # PROPERTIES
    # =========================================================================

    @property
    def session_id(self) -> str:
        return self.metadata.session_id

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def total_signals(self) -> int:
        return len(self._signals)

    @property
    def telegram_enabled(self) -> bool:
        return self.metadata.telegram_enabled

    @property
    def discord_enabled(self) -> bool:
        return self.metadata.discord_enabled
