"""Live traffic runner: plays a profile against the protected app through the tunnel.

traffic <profile> [--duration SECONDS] [--seed N]
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from profiles import PROFILES, Action

BASE_URL = "http://10.13.0.1"


def perform(a: Action) -> int:
    data = urllib.parse.urlencode(a.form).encode() if a.form else None
    req = urllib.request.Request(
        BASE_URL + a.path, data=data, method=a.method, headers={"User-Agent": a.ua}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            while resp.read(1 << 16):
                pass
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0  # blocked (quarantined) or gateway unreachable


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("profile", choices=sorted(PROFILES))
    p.add_argument("--duration", type=float, default=0, help="seconds, 0 = forever")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    rng = random.Random(args.seed)
    deadline = time.monotonic() + args.duration if args.duration else float("inf")
    stats: dict[int, int] = {}
    for action in PROFILES[args.profile](rng):
        if time.monotonic() >= deadline:
            break
        code = perform(action)
        stats[code] = stats.get(code, 0) + 1
        if code == 0:
            time.sleep(1)  # avoid a hot loop while isolated
        time.sleep(action.sleep)
    print(f"[{args.profile}] status counts: {dict(sorted(stats.items()))}", file=sys.stderr)


if __name__ == "__main__":
    main()
