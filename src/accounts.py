"""SHIELD visitor accounts: Google sign-in, sessions and per-visitor API spend.

Heroku's filesystem is wiped on every restart, so accounts live in a Google
Sheet (tab "users", one row per visitor) read and written through the Sheets
API with the app's service account. The Sheet is read once, then kept in
memory: spend is added in memory on every API call and the changed rows are
written back in one batch every FLUSH_SECONDS, at the end of each WebSocket
session and at shutdown. A crash can lose at most FLUSH_SECONDS of metering.

Without SHIELD_USERS_SHEET_ID (local dev) the same rows go to a JSON file.

Env:
    GOOGLE_CLIENT_ID        OAuth web client id of the "Sign in with Google" button
    SHIELD_USERS_SHEET_ID   spreadsheet id, shared (Editor) with the service account
    SESSION_SECRET          signs the session cookie; without it, sessions end
                            at each restart (visitors just sign in again)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.pricing import FREE_CREDIT_USD

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
SHEET_ID = os.environ.get("SHIELD_USERS_SHEET_ID", "")
FLUSH_SECONDS = 30

_TAB = "users"
_HEADER = ["participant", "email", "name", "interest", "registered_at",
           "last_seen", "spend_usd", "spend_updated_at"]
_LOCAL_FILE = Path(__file__).resolve().parent.parent / "data" / "accounts" / "accounts.json"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string (second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def participant_for(google_sub: str) -> str:
    """Stable participant id for a Google account (logs, analysis PDF)."""
    return "g-" + hashlib.sha256(google_sub.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------

def verify_google_credential(credential: str) -> Dict[str, str]:
    """Check a Sign-in-with-Google ID token. Raises ValueError if invalid."""
    if not GOOGLE_CLIENT_ID:
        raise ValueError("Google sign-in is not configured")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    info = id_token.verify_oauth2_token(credential, g_requests.Request(), GOOGLE_CLIENT_ID)
    if not info.get("email_verified"):
        raise ValueError("Google account email is not verified")
    return {"sub": info["sub"], "email": info["email"], "name": info.get("name", "")}


# ---------------------------------------------------------------------------
# Session cookie: "<participant>.<expiry>.<hmac>"
# ---------------------------------------------------------------------------

SESSION_COOKIE = "shield_session"
SESSION_MAX_AGE = 90 * 24 * 3600

_secret = os.environ.get("SESSION_SECRET", "")
if not _secret:
    logger.warning("SESSION_SECRET not set — sessions will not survive a restart")
    _secret = secrets.token_hex(32)
_SECRET = _secret.encode()


def _sign(payload: str) -> str:
    """Return the truncated HMAC-SHA256 signature of a cookie payload."""
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_session(participant: str) -> str:
    """Build a signed session cookie value for a participant, valid for SESSION_MAX_AGE seconds."""
    payload = f"{participant}.{int(time.time()) + SESSION_MAX_AGE}"
    return f"{payload}.{_sign(payload)}"


def read_session(cookie: Optional[str]) -> Optional[str]:
    """Participant id of a valid session cookie, else None."""
    if not cookie or cookie.count(".") != 2:
        return None
    participant, expiry, sig = cookie.split(".")
    payload = f"{participant}.{expiry}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    if not expiry.isdigit() or int(expiry) < time.time():
        return None
    return participant


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class _SheetBackend:
    """Rows of the "users" tab via the Sheets API (service account)."""

    def __init__(self, sheet_id: str):
        """Open an authorized Google Sheets session for the given spreadsheet id."""
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self._http = AuthorizedSession(creds)
        self._base = f"{_SHEETS_API}/{sheet_id}"

    def _call(self, method: str, url: str, **kw) -> Dict[str, Any]:
        """Send a request to the Sheets API and return the JSON body; raise RuntimeError on HTTP errors."""
        resp = self._http.request(method, self._base + url, timeout=20, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"Sheets API {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def read_all(self) -> List[List[Any]]:
        """Return all rows of the accounts tab (header included), or only the header if the tab is missing."""
        try:
            data = self._call("GET", f"/values/{_TAB}!A1:H",
                              params={"valueRenderOption": "UNFORMATTED_VALUE"})
        except RuntimeError as exc:
            if "Unable to parse range" not in str(exc):
                raise
            # First run: create the tab with its header
            self._call("POST", ":batchUpdate",
                       json={"requests": [{"addSheet": {"properties": {"title": _TAB}}}]})
            data = {}
        rows = data.get("values", [])
        if not rows:
            self._call("PUT", f"/values/{_TAB}!A1:H1", params={"valueInputOption": "RAW"},
                       json={"values": [_HEADER]})
            rows = [_HEADER]
        return rows

    def append(self, row: List[Any]) -> int:
        """Append a row to the accounts tab and return its 1-based row number."""
        data = self._call("POST", f"/values/{_TAB}!A1:append",
                          params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                          json={"values": [row]})
        # "users!A12:H12" -> 12
        return int(re.search(r"![A-Z]+(\d+)", data["updates"]["updatedRange"]).group(1))

    def update_rows(self, updates: Dict[int, List[Any]]) -> None:
        """Overwrite columns F-H (last_seen, spend) of the given 1-based rows in one batch request."""
        self._call("POST", "/values:batchUpdate", json={
            "valueInputOption": "RAW",
            "data": [{"range": f"{_TAB}!F{n}:H{n}", "values": [vals]} for n, vals in updates.items()],
        })


class _FileBackend:
    """Same rows in a local JSON file (dev only: Heroku wipes it)."""

    def read_all(self) -> List[List[Any]]:
        """Return all rows from the local JSON file, or only the header if it does not exist."""
        if _LOCAL_FILE.exists():
            return json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
        return [_HEADER]

    def _write(self, rows: List[List[Any]]) -> None:
        """Write all rows to the local JSON file, creating its folder if needed."""
        _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_FILE.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")

    def append(self, row: List[Any]) -> int:
        """Append a row to the local file and return its 1-based row number."""
        rows = self.read_all()
        rows.append(row)
        self._write(rows)
        return len(rows)

    def update_rows(self, updates: Dict[int, List[Any]]) -> None:
        """Overwrite columns F-H of the given 1-based rows in the local file."""
        rows = self.read_all()
        for n, vals in updates.items():
            rows[n - 1][5:8] = vals
        self._write(rows)


class UserStore:
    """In-memory view of the accounts sheet; every method is blocking."""

    def __init__(self) -> None:
        """Create an empty store; the backend is loaded lazily on first access."""
        self._lock = threading.RLock()
        self._backend: Any = None
        self._users: Dict[str, Dict[str, Any]] = {}
        self._rows: Dict[str, int] = {}
        self._dirty: set = set()

    def _ensure_loaded(self) -> None:
        """Load every account from the Sheet (or local file) into memory once."""
        if self._backend is not None:
            return
        backend = _SheetBackend(SHEET_ID) if SHEET_ID else _FileBackend()
        rows = backend.read_all()
        for n, row in enumerate(rows[1:], start=2):
            rec = dict(zip(_HEADER, row + [""] * (len(_HEADER) - len(row))))
            if not rec["participant"]:
                continue
            try:
                rec["spend_usd"] = float(rec["spend_usd"] or 0)
            except (TypeError, ValueError):
                rec["spend_usd"] = 0.0
            self._users[rec["participant"]] = rec
            self._rows[rec["participant"]] = n
        self._backend = backend
        logger.info("[accounts] %d accounts loaded from %s", len(self._users),
                    "Sheet" if SHEET_ID else _LOCAL_FILE)

    def get(self, participant: str) -> Optional[Dict[str, Any]]:
        """Return a copy of the participant's account record, or None if unknown."""
        with self._lock:
            self._ensure_loaded()
            rec = self._users.get(participant)
            return dict(rec) if rec else None

    def register(self, participant: str, email: str, name: str, interest: str) -> Dict[str, Any]:
        """Create the visitor's row (written to the Sheet right away)."""
        with self._lock:
            self._ensure_loaded()
            if participant in self._users:
                return dict(self._users[participant])
            now = _now()
            rec = {"participant": participant, "email": email, "name": name,
                   "interest": interest, "registered_at": now, "last_seen": now,
                   "spend_usd": 0.0, "spend_updated_at": ""}
            self._rows[participant] = self._backend.append([rec[k] for k in _HEADER])
            self._users[participant] = rec
            return dict(rec)

    def touch(self, participant: str) -> None:
        """Update the participant's last_seen time and mark the row for the next flush."""
        with self._lock:
            self._ensure_loaded()
            if participant in self._users:
                self._users[participant]["last_seen"] = _now()
                self._dirty.add(participant)

    def add_spend(self, participant: str, usd: float) -> None:
        """Add a positive USD amount to the participant's metered spend and mark the row dirty."""
        if usd <= 0:
            return
        with self._lock:
            self._ensure_loaded()
            rec = self._users.get(participant)
            if rec is None:
                return
            rec["spend_usd"] += usd
            rec["spend_updated_at"] = _now()
            self._dirty.add(participant)

    def credit_left(self, participant: str) -> float:
        """Return the participant's remaining free credit in USD (0 if unknown or exhausted)."""
        rec = self.get(participant)
        return max(0.0, FREE_CREDIT_USD - rec["spend_usd"]) if rec else 0.0

    def flush(self) -> None:
        """Write changed rows (last_seen, spend) in one request."""
        with self._lock:
            if self._backend is None or not self._dirty:
                return
            pending = {p: self._users[p] for p in self._dirty}
            self._dirty = set()
        updates = {self._rows[p]: [r["last_seen"], round(r["spend_usd"], 6), r["spend_updated_at"]]
                   for p, r in pending.items()}
        try:
            self._backend.update_rows(updates)
        except Exception:
            logger.exception("[accounts] flush failed — will retry")
            with self._lock:
                self._dirty |= set(pending)


store = UserStore()
