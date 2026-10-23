"""Small SQLite log of finished reviews (what ran, what was found, what was posted)."""

import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, data_dir: str | Path):
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.path = path / "reviews.db"
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS reviews ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, repo TEXT, pr INTEGER, "
                "head_sha TEXT, status TEXT, detail TEXT)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record(self, repo: str, pr: int, head_sha: str, status: str, detail: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO reviews (created, repo, pr, head_sha, status, detail) VALUES (?,?,?,?,?,?)",
                (time.time(), repo, pr, head_sha, status, json.dumps(detail)),
            )

    def recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT created, repo, pr, head_sha, status, detail FROM reviews "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"created": r[0], "repo": r[1], "pr": r[2], "head_sha": r[3], "status": r[4],
             "detail": json.loads(r[5])}
            for r in rows
        ]  # fmt: skip
