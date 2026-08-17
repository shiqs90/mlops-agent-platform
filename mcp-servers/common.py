"""Shared Postgres access for the three MCP servers.

One connection pool per process. The servers are read-only against the banking
dataset — the single write path (initiate_transfer) is deliberately isolated in
accounts.py so the human-approval middleware has exactly one thing to guard.
"""

import os
from contextlib import contextmanager
from decimal import Decimal

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=os.environ.get("PGHOST", "postgres.nova.svc.cluster.local"),
            dbname=os.environ.get("PGDATABASE", "nova"),
            user=os.environ.get("PGUSER", "nova"),
            password=os.environ["PGPASSWORD"],
        )
    return _pool


@contextmanager
def cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    finally:
        pool.putconn(conn)


def jsonable(rows):
    """Decimals and dates aren't JSON-serialisable, and the model reads this output.

    Money becomes a float rather than a string: the agent has to compare and sum
    these values, and a string would push that work into the prompt where it gets
    done unreliably. Precision loss is irrelevant at AED scale.
    """
    out = []
    for row in rows:
        clean = {}
        for k, v in dict(row).items():
            if isinstance(v, Decimal):
                clean[k] = float(v)
            elif hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            else:
                clean[k] = v
        out.append(clean)
    return out


def one(rows):
    return jsonable(rows)[0] if rows else None
