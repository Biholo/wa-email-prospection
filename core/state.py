import threading

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "processed": 0,
    "errors": 0,
    "config": None,
    "liste_id": None,
    "started_at": None,
}


def get_state() -> dict:
    with _lock:
        return _state.copy()


def update_state(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def increment_processed() -> None:
    with _lock:
        _state["processed"] += 1


def increment_errors() -> None:
    with _lock:
        _state["errors"] += 1
