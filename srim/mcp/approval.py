"""One-time approval codes for the agent's ask-first actions (Qt-free).

When a call needs the user's OK and the client cannot ask the user itself, the server hands the
agent a code bound to exactly that action. The agent repeats the call with the code; the code
works once, only for the same action, and expires after a few minutes. Codes are held in memory.
"""

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable

# No look-alike characters (0/O, 1/I/L, 5/S): the user may read a code aloud or type it.
ALPHABET = "2346789ABCDEFGHJKMNPQRTUVWXYZ"
CODE_LENGTH = 6


def action_hash(tool: str, args: dict, setup: dict | None, workers: int | None) -> str:
    """The SHA-256 (hex) of one action: the tool, its arguments, the setup and the workers.

    The ``confirm_code`` argument is not part of the action, so the call that carries the code
    has the same hash as the call that asked for it.
    """
    arguments = {key: value for key, value in args.items() if key != "confirm_code"}
    payload = {"tool": tool, "args": arguments, "setup": setup, "workers": workers}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ApprovalCodes:
    """Issues codes for actions and redeems them once, while they are live."""

    def __init__(self, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._live: dict[str, tuple[str, float]] = {}  # code -> (action, expiry time)
        self._lock = threading.Lock()

    def issue(self, action: str) -> str:
        """A new code for ``action``, unique among the codes that are still live."""
        with self._lock:
            self._forget_expired()
            while True:
                code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
                if code not in self._live:
                    break
            self._live[code] = (action, self._clock() + self.ttl)
            return code

    def redeem(self, code: str, action: str) -> bool:
        """True once when ``code`` was issued for ``action`` and is still live.

        A code given for another action is refused and stays usable for its own action.
        """
        key = code.strip().upper()
        with self._lock:
            self._forget_expired()
            entry = self._live.get(key)
            if entry is None or entry[0] != action:
                return False
            del self._live[key]
            return True

    def _forget_expired(self) -> None:
        now = self._clock()
        for code in [c for c, (_, expiry) in self._live.items() if now >= expiry]:
            del self._live[code]
