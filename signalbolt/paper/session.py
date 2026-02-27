"""
Paper trading session management.

Features:
- Session state persistence
- Automatic saving on each scan
- Session metadata tracking
- Multiple session support
- Resume from any point

Usage:
    # Create new session
    session = PaperSession.create(
        name="my_session",
        initial_balance=1000.0
    )

    # Save after each scan
    session.save_checkpoint()

    # Later: load session
    session = PaperSession.load("my_session")

    # Check if offline replay needed
    if session.needs_replay():
        # Replay historical data
        session.replay_offline_period()
"""

import os
import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid

from signalbolt.core.config import Config
from signalbolt.paper.portfolio import PaperPortfolio, Position, TradeResult
from signalbolt.utils.logger import get_logger

log = get_logger("signalbolt.paper.session")


# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
SESSIONS_DIR = DATA_DIR / "paper_sessions"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# SESSION STATE
# =============================================================================


class SessionStatus(Enum):
    """Session status."""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    OFFLINE = "offline"  # User went offline, needs replay


@dataclass
class SessionCheckpoint:
    """Checkpoint at specific moment."""

    checkpoint_id: str
    timestamp: datetime
    scan_number: int

    # Portfolio state
    balance: float
    unrealized_pnl: float
    open_positions: int
    total_trades: int

    # Last activity
    last_signal_time: Optional[datetime] = None
    last_trade_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "timestamp": self.timestamp.isoformat(),
            "scan_number": self.scan_number,
            "balance": self.balance,
            "unrealized_pnl": self.unrealized_pnl,
            "open_positions": self.open_positions,
            "total_trades": self.total_trades,
            "last_signal_time": self.last_signal_time.isoformat()
            if self.last_signal_time
            else None,
            "last_trade_time": self.last_trade_time.isoformat()
            if self.last_trade_time
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionCheckpoint":
        data = dict(data)
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])

        if data.get("last_signal_time"):
            data["last_signal_time"] = datetime.fromisoformat(data["last_signal_time"])
        if data.get("last_trade_time"):
            data["last_trade_time"] = datetime.fromisoformat(data["last_trade_time"])

        return cls(**data)


@dataclass
class SessionMetadata:
    """Session metadata."""

    session_id: str
    name: str

    # Creation
    created_at: datetime
    config_name: str
    initial_balance: float

    # Status
    status: SessionStatus

    # Tracking
    started_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None

    # Stats
    total_scans: int = 0
    total_signals: int = 0
    total_trades: int = 0
    total_runtime_minutes: float = 0.0

    # Offline tracking
    offline_periods: List[Dict] = field(default_factory=list)

    # Description
    description: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "config_name": self.config_name,
            "initial_balance": self.initial_balance,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_active_at": self.last_active_at.isoformat()
            if self.last_active_at
            else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "total_scans": self.total_scans,
            "total_signals": self.total_signals,
            "total_trades": self.total_trades,
            "total_runtime_minutes": self.total_runtime_minutes,
            "offline_periods": self.offline_periods,
            "description": self.description,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetadata":
        data = dict(data)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["status"] = SessionStatus(data["status"])

        if data.get("started_at"):
            data["started_at"] = datetime.fromisoformat(data["started_at"])
        if data.get("last_active_at"):
            data["last_active_at"] = datetime.fromisoformat(data["last_active_at"])
        if data.get("stopped_at"):
            data["stopped_at"] = datetime.fromisoformat(data["stopped_at"])

        return cls(**data)


# =============================================================================
# PAPER SESSION
# =============================================================================


class PaperSession:
    """
    Paper trading session with offline support.

    Manages:
    - Session lifecycle (create, start, pause, stop)
    - Portfolio state persistence
    - Checkpoint saving (after each scan)
    - Offline period detection and replay
    - Statistics tracking
    """

    def __init__(
        self,
        session_dir: Path,
        metadata: SessionMetadata,
        portfolio: PaperPortfolio,
        config: Config,
    ):
        """
        Initialize session (use create() or load() instead).
        """
        self.session_dir = session_dir
        self.metadata = metadata
        self.portfolio = portfolio
        self.config = config

        # Runtime state
        self._scan_counter = metadata.total_scans
        self._last_checkpoint: Optional[SessionCheckpoint] = None
        self._session_start_time: Optional[datetime] = None

        # Watched symbols (for replay)
        self._watched_symbols: List[str] = []

        # Replay info
        self._needs_replay = False
        self._replay_start: Optional[datetime] = None
        self._replay_end: Optional[datetime] = None

    # =========================================================================
    # FACTORY METHODS
    # =========================================================================

    @classmethod
    def create(
        cls,
        name: str,
        initial_balance: float = 1000.0,
        config: Optional[Config] = None,
        config_name: str = "default",
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> "PaperSession":
        """
        Create new paper trading session.

        Args:
            name: Session name
            initial_balance: Starting balance
            config: Config instance
            config_name: Config name for reference
            description: Session description
            tags: Session tags

        Returns:
            PaperSession instance
        """
        config = config or Config()

        # Generate session ID
        session_id = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create session directory
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Create metadata
        metadata = SessionMetadata(
            session_id=session_id,
            name=name,
            created_at=datetime.now(),
            config_name=config_name,
            initial_balance=initial_balance,
            status=SessionStatus.ACTIVE,
            description=description,
            tags=tags or [],
        )

        # Create portfolio
        portfolio = PaperPortfolio(
            initial_balance=initial_balance,
            taker_fee_pct=config.get("exchange", "taker_fee_pct", default=0.04),
            maker_fee_pct=config.get("exchange", "maker_fee_pct", default=0.0),
            max_positions=config.get("spot", "max_positions", default=1),
        )

        # Create session
        session = cls(
            session_dir=session_dir,
            metadata=metadata,
            portfolio=portfolio,
            config=config,
        )

        # Save initial state
        session._save_metadata()
        session._save_portfolio()
        session.save_checkpoint()

        log.info(f"Created session: {session_id} (balance: ${initial_balance})")

        return session

    @classmethod
    def load(cls, session_id: str, config: Optional[Config] = None) -> "PaperSession":
        """
        Load existing session.

        Args:
            session_id: Session ID or name
            config: Config instance

        Returns:
            PaperSession instance
        """
        config = config or Config()

        # Find session directory
        session_dir = SESSIONS_DIR / session_id

        if not session_dir.exists():
            # Try to find by name prefix
            matching = list(SESSIONS_DIR.glob(f"{session_id}*"))
            if matching:
                session_dir = matching[-1]  # Most recent
            else:
                raise FileNotFoundError(f"Session not found: {session_id}")

        # Load metadata
        metadata_file = session_dir / "metadata.json"
        with open(metadata_file, "r") as f:
            metadata = SessionMetadata.from_dict(json.load(f))

        # Load portfolio
        portfolio_file = session_dir / "portfolio.json"
        with open(portfolio_file, "r") as f:
            portfolio = PaperPortfolio.from_dict(json.load(f))

        # Create session
        session = cls(
            session_dir=session_dir,
            metadata=metadata,
            portfolio=portfolio,
            config=config,
        )

        # Load last checkpoint
        session._load_last_checkpoint()

        # Check if needs replay
        session._check_offline_period()

        log.info(f"Loaded session: {metadata.session_id}")

        if session._needs_replay:
            log.warning(
                f"⚠️ Session was offline! Needs replay from {session._replay_start}"
            )

        return session

    @classmethod
    def list_sessions(cls) -> List[Dict]:
        """List all available sessions."""
        sessions = []

        for session_dir in SESSIONS_DIR.iterdir():
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
                                "status": data["status"],
                                "initial_balance": data["initial_balance"],
                                "total_trades": data["total_trades"],
                                "last_active": data.get("last_active_at"),
                            }
                        )
                    except:
                        pass

        # Sort by creation date (newest first)
        sessions.sort(key=lambda x: x["created_at"], reverse=True)

        return sessions

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def start(self):
        """Start/resume session."""
        self._session_start_time = datetime.now()

        if self.metadata.started_at is None:
            self.metadata.started_at = datetime.now()

        self.metadata.status = SessionStatus.ACTIVE
        self.metadata.last_active_at = datetime.now()

        self._save_metadata()

        log.info(f"Session started: {self.metadata.session_id}")

    def pause(self):
        """Pause session."""
        self.metadata.status = SessionStatus.PAUSED
        self.metadata.last_active_at = datetime.now()

        self._update_runtime()
        self._save_all()

        log.info(f"Session paused: {self.metadata.session_id}")

    def stop(self):
        """Stop session permanently."""
        self.metadata.status = SessionStatus.STOPPED
        self.metadata.stopped_at = datetime.now()
        self.metadata.last_active_at = datetime.now()

        self._update_runtime()
        self._save_all()

        log.info(f"Session stopped: {self.metadata.session_id}")

    def _update_runtime(self):
        """Update total runtime."""
        if self._session_start_time:
            elapsed = (datetime.now() - self._session_start_time).total_seconds() / 60
            self.metadata.total_runtime_minutes += elapsed
            self._session_start_time = datetime.now()

    # =========================================================================
    # CHECKPOINTS
    # =========================================================================

    def save_checkpoint(self):
        """
        Save checkpoint (call after each scan).

        This is the key for offline recovery!
        """
        self._scan_counter += 1
        self.metadata.total_scans = self._scan_counter
        self.metadata.last_active_at = datetime.now()

        # Create checkpoint
        checkpoint = SessionCheckpoint(
            checkpoint_id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(),
            scan_number=self._scan_counter,
            balance=self.portfolio.total_balance,
            unrealized_pnl=self.portfolio.unrealized_pnl,
            open_positions=self.portfolio.open_position_count,
            total_trades=len(self.portfolio._trade_results),
        )

        self._last_checkpoint = checkpoint

        # Save everything
        self._save_all()

        # Save checkpoint to history
        self._append_checkpoint(checkpoint)

        log.debug(f"Checkpoint saved: scan #{self._scan_counter}")

    def _append_checkpoint(self, checkpoint: SessionCheckpoint):
        """Append checkpoint to history file."""
        checkpoints_file = self.session_dir / "checkpoints.jsonl"

        with open(checkpoints_file, "a") as f:
            f.write(json.dumps(checkpoint.to_dict()) + "\n")

    def _load_last_checkpoint(self):
        """Load last checkpoint from history."""
        checkpoints_file = self.session_dir / "checkpoints.jsonl"

        if not checkpoints_file.exists():
            return

        # Read last line
        with open(checkpoints_file, "r") as f:
            lines = f.readlines()

        if lines:
            last_line = lines[-1].strip()
            if last_line:
                self._last_checkpoint = SessionCheckpoint.from_dict(
                    json.loads(last_line)
                )
                self._scan_counter = self._last_checkpoint.scan_number

    def get_checkpoints(self, limit: int = 100) -> List[SessionCheckpoint]:
        """Get recent checkpoints."""
        checkpoints_file = self.session_dir / "checkpoints.jsonl"

        if not checkpoints_file.exists():
            return []

        checkpoints = []

        with open(checkpoints_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    checkpoints.append(SessionCheckpoint.from_dict(json.loads(line)))

        return checkpoints[-limit:]

    # =========================================================================
    # OFFLINE DETECTION
    # =========================================================================

    def _check_offline_period(self):
        """Check if session was offline and needs replay."""
        if not self._last_checkpoint:
            return

        last_active = self._last_checkpoint.timestamp
        now = datetime.now()

        # Threshold: if more than 10 minutes since last scan, consider offline
        offline_threshold = timedelta(minutes=10)

        if now - last_active > offline_threshold:
            self._needs_replay = True
            self._replay_start = last_active
            self._replay_end = now

            # Record offline period
            self.metadata.offline_periods.append(
                {
                    "start": last_active.isoformat(),
                    "end": now.isoformat(),
                    "duration_hours": (now - last_active).total_seconds() / 3600,
                }
            )

            log.info(f"Offline period detected: {last_active} → {now}")

    def needs_replay(self) -> bool:
        """Check if offline replay is needed."""
        return self._needs_replay

    def get_replay_period(self) -> Optional[Tuple[datetime, datetime]]:
        """Get offline period that needs replay."""
        if self._needs_replay:
            return (self._replay_start, self._replay_end)
        return None

    def mark_replay_complete(self):
        """Mark offline replay as complete."""
        self._needs_replay = False
        self._replay_start = None
        self._replay_end = None

        # Update last offline period with replay status
        if self.metadata.offline_periods:
            self.metadata.offline_periods[-1]["replayed"] = True
            self.metadata.offline_periods[-1]["replayed_at"] = (
                datetime.now().isoformat()
            )

        self._save_metadata()

        log.info("Offline replay marked as complete")

    # =========================================================================
    # WATCHED SYMBOLS
    # =========================================================================

    def set_watched_symbols(self, symbols: List[str]):
        """Set symbols being watched (for replay)."""
        self._watched_symbols = symbols
        self._save_watched_symbols()

    def add_watched_symbol(self, symbol: str):
        """Add symbol to watch list."""
        if symbol not in self._watched_symbols:
            self._watched_symbols.append(symbol)
            self._save_watched_symbols()

    def get_watched_symbols(self) -> List[str]:
        """Get watched symbols."""
        return self._watched_symbols

    def _save_watched_symbols(self):
        """Save watched symbols."""
        watched_file = self.session_dir / "watched_symbols.json"

        with open(watched_file, "w") as f:
            json.dump({"symbols": self._watched_symbols}, f)

    def _load_watched_symbols(self):
        """Load watched symbols."""
        watched_file = self.session_dir / "watched_symbols.json"

        if watched_file.exists():
            with open(watched_file, "r") as f:
                data = json.load(f)
                self._watched_symbols = data.get("symbols", [])

    # =========================================================================
    # PERSISTENCE
    # =========================================================================

    def _save_all(self):
        """Save all session state."""
        self._save_metadata()
        self._save_portfolio()
        self._save_watched_symbols()

    def _save_metadata(self):
        """Save metadata."""
        metadata_file = self.session_dir / "metadata.json"

        with open(metadata_file, "w") as f:
            json.dump(self.metadata.to_dict(), f, indent=2)

    def _save_portfolio(self):
        """Save portfolio."""
        portfolio_file = self.session_dir / "portfolio.json"

        with open(portfolio_file, "w") as f:
            json.dump(self.portfolio.to_dict(), f, indent=2)

    def export_trades(self, format: str = "json") -> str:
        """
        Export trade history.

        Args:
            format: 'json' or 'csv'

        Returns:
            File path
        """
        trades = [r.to_dict() for r in self.portfolio._trade_results]

        if format == "csv":
            import csv

            export_file = (
                self.session_dir / f"trades_{datetime.now():%Y%m%d_%H%M%S}.csv"
            )

            if trades:
                with open(export_file, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=trades[0].keys())
                    writer.writeheader()
                    writer.writerows(trades)
        else:
            export_file = (
                self.session_dir / f"trades_{datetime.now():%Y%m%d_%H%M%S}.json"
            )

            with open(export_file, "w") as f:
                json.dump(trades, f, indent=2)

        log.info(f"Exported {len(trades)} trades to {export_file}")

        return str(export_file)

    def delete(self, confirm: bool = False):
        """
        Delete session and all data.

        Args:
            confirm: Must be True to delete
        """
        if not confirm:
            raise ValueError("Set confirm=True to delete session")

        shutil.rmtree(self.session_dir)
        log.warning(f"Deleted session: {self.metadata.session_id}")

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_summary(self) -> dict:
        """Get session summary."""
        portfolio_stats = self.portfolio.get_stats()

        return {
            "session_id": self.metadata.session_id,
            "name": self.metadata.name,
            "status": self.metadata.status.value,
            "created_at": self.metadata.created_at.isoformat(),
            "last_active": self.metadata.last_active_at.isoformat()
            if self.metadata.last_active_at
            else None,
            "total_runtime_minutes": round(self.metadata.total_runtime_minutes, 1),
            "total_scans": self.metadata.total_scans,
            "offline_periods": len(self.metadata.offline_periods),
            "needs_replay": self._needs_replay,
            "portfolio": portfolio_stats,
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
    def status(self) -> SessionStatus:
        return self.metadata.status

    @property
    def is_active(self) -> bool:
        return self.metadata.status == SessionStatus.ACTIVE

    @property
    def balance(self) -> float:
        return self.portfolio.total_balance

    @property
    def open_positions(self) -> List[Position]:
        return self.portfolio.open_positions


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def create_session(
    name: str, initial_balance: float = 1000.0, config: Optional[Config] = None
) -> PaperSession:
    """Create new paper session."""
    return PaperSession.create(
        name=name, initial_balance=initial_balance, config=config
    )


def load_session(session_id: str, config: Optional[Config] = None) -> PaperSession:
    """Load existing session."""
    return PaperSession.load(session_id, config)


def list_sessions() -> List[Dict]:
    """List all sessions."""
    return PaperSession.list_sessions()
