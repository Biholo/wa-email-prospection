import threading
from collections import defaultdict
from datetime import datetime, timedelta

_lock = threading.Lock()
_store: dict[str, list[datetime]] = defaultdict(list)

_MAX = 5
_WINDOW = timedelta(hours=1)


def check_rate_limit(ip: str) -> bool:
    """Returns True if allowed, False if rate limited (5 req/IP/hour)."""
    now = datetime.utcnow()
    with _lock:
        _store[ip] = [t for t in _store[ip] if now - t < _WINDOW]
        if len(_store[ip]) >= _MAX:
            return False
        _store[ip].append(now)
        return True
