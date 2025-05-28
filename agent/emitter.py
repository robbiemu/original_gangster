import json
from typing import Any, Callable, Dict


# --- NDJSON emitter for IPC ---------------------------------------------------
def emit(msg_type: str, data: dict):
    payload = {"type": msg_type, **data}
    print(json.dumps(payload), flush=True)
    

_EmitterCallable = Callable[[str, Dict[str, Any]], None]
