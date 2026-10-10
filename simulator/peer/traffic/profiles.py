"""Behaviour profiles for simulated devices, shared by the live runner (real HTTP through
the tunnel) and the offline dataset generator (ml/ztna_ml/datagen.py).

A profile is a generator of Actions; `sleep` is the think time after the request.
Stdlib only.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

PAGES = ["home", "reports", "wiki", "tickets", "settings"]
BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]
CLI_UAS = ["curl/8.5.0", "python-requests/2.32.3", "HTTPie/3.2.3"]
# Background software on a managed device (posture agent, updaters, sync clients).
BACKGROUND_UAS = ["ZTNA-Agent/1.4 (posture-check)", "curl/7.88.1", "Microsoft-WNS/10.0"]
SCANNER_UAS = [
    "Mozilla/5.00 (Nikto/2.5.0) (Evasions:None) (Test:000001)",
    "sqlmap/1.8.9#stable (https://sqlmap.org)",
    "gobuster/3.6",
]
ENUM_WORDS = [
    "admin",
    "backup",
    "config",
    ".env",
    ".git/HEAD",
    "wp-admin",
    "wp-login.php",
    "phpmyadmin",
    "server-status",
    "api/v1/users",
    "api/v2",
    "debug",
    "console",
    "actuator/health",
    "old",
    "test",
    "dev",
    "staging",
    "uploads",
    "private",
    "db.sql",
    "dump.sql",
    "robots.txt",
    "sitemap.xml",
    "login.php",
    "admin.php",
    "cgi-bin/",
    "shell.php",
    "manager/html",
    "jenkins",
]
PASSWORDS = [
    "123456",
    "password",
    "qwerty",
    "letmein",
    "admin",
    "welcome",
    "monkey",
    "dragon",
    "iloveyou",
    "sunshine",
    "princess",
    "football",
    "master",
    "hello123",
    "trustno1",
]
LOGIN_OK = {"username": "alice", "password": "correct-horse"}


@dataclass(frozen=True)
class Action:
    method: str
    path: str
    ua: str
    sleep: float
    form: dict[str, str] | None = None


def think(rng: random.Random, median: float, sigma: float = 0.8) -> float:
    """Log-normal think time: mostly short gaps, occasional long pauses."""
    return min(rng.lognormvariate(0, sigma) * median, median * 15)


def browser(rng: random.Random) -> Iterator[Action]:
    """Office user browsing the portal."""
    ua = rng.choice(BROWSER_UAS)
    background = rng.choice(BACKGROUND_UAS)
    yield Action("POST", "/login", ua, think(rng, 2), form=LOGIN_OK)
    while True:
        r = rng.random()
        if rng.random() < 0.04:
            yield Action("GET", "/", background, think(rng, 2))
        elif r < 0.55:
            yield Action("GET", f"/pages/{rng.choice(PAGES)}", ua, think(rng, 3))
        elif r < 0.75:
            yield Action("GET", "/", ua, think(rng, 2))
        elif r < 0.87:
            yield Action("GET", "/api/items", ua, think(rng, 2))
        elif r < 0.95:
            yield Action("GET", f"/files/{rng.randint(20, 3000)}", ua, think(rng, 5))
        elif r < 0.98:
            # a typo now and then, followed by the right password
            yield Action(
                "POST",
                "/login",
                ua,
                think(rng, 1.5),
                form={"username": "alice", "password": "correct-hrose"},
            )
            yield Action("POST", "/login", ua, think(rng, 2), form=LOGIN_OK)
        else:
            yield Action("GET", f"/pages/{rng.choice(['wikii', 'report'])}", ua, think(rng, 2))


def developer(rng: random.Random) -> Iterator[Action]:
    """Engineer using CLI tools against the API, with occasional bulk exports."""
    ua = rng.choice(CLI_UAS)
    background = rng.choice(BACKGROUND_UAS)
    while True:
        r = rng.random()
        if rng.random() < 0.04:
            yield Action("GET", "/", background, think(rng, 2))
        elif r < 0.75:
            yield Action("GET", "/api/items", ua, think(rng, 1.2, 0.6))
        elif r < 0.85:
            yield Action("POST", "/login", ua, think(rng, 1), form=LOGIN_OK)
        elif r < 0.97:
            yield Action("GET", f"/files/{rng.randint(500, 8000)}", ua, think(rng, 4))
        else:
            yield Action("GET", f"/files/{rng.randint(10_000, 25_000)}", ua, think(rng, 20))


def bruteforce(rng: random.Random) -> Iterator[Action]:
    ua = rng.choice(BROWSER_UAS)
    while True:
        pw = rng.choice(PASSWORDS) + str(rng.randint(0, 99))
        yield Action(
            "POST",
            "/login",
            ua,
            rng.uniform(0.05, 0.3),
            form={"username": rng.choice(["alice", "admin", "root"]), "password": pw},
        )


def slow_bruteforce(rng: random.Random) -> Iterator[Action]:
    """Low-and-slow guessing that stays under the per-window rule threshold."""
    ua = rng.choice(BROWSER_UAS)
    while True:
        yield Action(
            "POST",
            "/login",
            ua,
            rng.uniform(4.5, 6.0),
            form={"username": "alice", "password": rng.choice(PASSWORDS)},
        )


def scanner(rng: random.Random) -> Iterator[Action]:
    ua = rng.choice(SCANNER_UAS)
    words = ENUM_WORDS[:]
    while True:
        rng.shuffle(words)
        for w in words:
            yield Action("GET", f"/{w}", ua, rng.uniform(0.05, 0.2))


def enumeration(rng: random.Random) -> Iterator[Action]:
    """Directory brute force hiding behind a normal browser UA."""
    ua = rng.choice(BROWSER_UAS)
    while True:
        w = rng.choice(ENUM_WORDS)
        suffix = rng.choice(["", ".bak", ".old", "~", "/", ".zip"])
        yield Action("GET", f"/{w}{suffix}", ua, rng.uniform(0.1, 0.5))


def exfiltration(rng: random.Random) -> Iterator[Action]:
    ua = rng.choice(BROWSER_UAS)
    while True:
        yield Action("GET", f"/files/{rng.randint(15_000, 30_000)}", ua, rng.uniform(0.2, 1.0))


def _blend(rng: random.Random, attack_share: float, attack) -> Iterator[Action]:
    """Interleave malicious actions into a real browsing session (same UA, human pacing)."""
    session = browser(rng)
    ua = next(session).ua  # the browser's login
    while True:
        if rng.random() < attack_share:
            yield attack(ua)
        else:
            yield next(session)


def stealth_bruteforce(rng: random.Random) -> Iterator[Action]:
    def guess(ua: str) -> Action:
        pw = rng.choice(PASSWORDS) + str(rng.randint(0, 99))
        form = {"username": rng.choice(["alice", "bob", "carol"]), "password": pw}
        return Action("POST", "/login", ua, think(rng, 2.5), form=form)

    return _blend(rng, 0.3, guess)


def stealth_exfiltration(rng: random.Random) -> Iterator[Action]:
    def pull(ua: str) -> Action:
        return Action("GET", f"/files/{rng.randint(4_000, 12_000)}", ua, think(rng, 3))

    return _blend(rng, 0.35, pull)


def stealth_enumeration(rng: random.Random) -> Iterator[Action]:
    def probe(ua: str) -> Action:
        return Action("GET", f"/{rng.choice(ENUM_WORDS)}", ua, think(rng, 2))

    return _blend(rng, 0.3, probe)


def odd_client(rng: random.Random) -> Iterator[Action]:
    """Unusual tooling sweeping the API: rare UA, high rate, no scanner signature."""
    ua = rng.choice(["Go-http-client/1.1", "okhttp/4.12.0", "Java/17.0.2", "libwww-perl/6.72"])
    while True:
        yield Action(
            "GET", rng.choice(["/api/items", "/", "/pages/settings"]), ua, rng.uniform(0.05, 0.25)
        )


NORMAL = {"browser": browser, "developer": developer}
ATTACKS = {
    "bruteforce": bruteforce,
    "slow-bruteforce": slow_bruteforce,
    "scanner": scanner,
    "enumeration": enumeration,
    "exfiltration": exfiltration,
    "odd-client": odd_client,
    "stealth-bruteforce": stealth_bruteforce,
    "stealth-exfiltration": stealth_exfiltration,
    "stealth-enumeration": stealth_enumeration,
}
PROFILES = {**NORMAL, **ATTACKS}
