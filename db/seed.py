"""Generate Nova's banking dataset.

DETERMINISM IS THE POINT. The golden set's expected answers are computed with SQL
against this data, so the data must be identical every run. Two rules enforce that:

  1. A fixed RNG seed, and generation in a fixed order.
  2. A fixed ANCHOR_DATE. Never `date.today()` — relative dates would shift the
     data on every run and silently rot every golden answer that mentions a month.

Balances are derived from transactions rather than generated independently, so
"what's my balance" and "sum my transactions" agree. If they didn't, the
groundedness judge would flag correct answers as unsupported.
"""

import io
import os
import random
import subprocess
from datetime import date, timedelta
from decimal import Decimal

import psycopg2

SEED = 42
ANCHOR_DATE = date(2026, 8, 1)   # "today" for this dataset — never move it
MONTHS_HISTORY = 24

N_CUSTOMERS = 2_000
N_ACCOUNTS = 3_000
N_TRANSACTIONS = 200_000

FIRST = ["Aisha", "Omar", "Fatima", "Yusuf", "Layla", "Hassan", "Noor", "Khalid",
         "Mariam", "Rashid", "Zainab", "Tariq", "Huda", "Salim", "Amina", "Faisal"]
LAST = ["Al-Mansouri", "Haddad", "Khoury", "Rahman", "Siddiqui", "Nasser", "Farouk",
        "Aziz", "Bakr", "Darwish", "Ghanem", "Hakim", "Ibrahim", "Jaber", "Karim"]

SEGMENTS = ["retail"] * 70 + ["premier"] * 25 + ["private"] * 5
ACCOUNT_TYPES = ["current"] * 55 + ["savings"] * 35 + ["fixed_deposit"] * 10

# (category, direction, min, max, monthly?) — monthly ones recur on a fixed day
SPEND = [
    ("salary",    "credit", 12_000, 45_000, True),
    ("rent",      "debit",   4_000, 15_000, True),
    ("utilities", "debit",     200,  1_200, True),
    ("groceries", "debit",      80,    900, False),
    ("dining",    "debit",      40,    600, False),
    ("transport", "debit",      15,    350, False),
    ("shopping",  "debit",     100,  2_500, False),
    ("transfer",  "debit",     500,  8_000, False),
]


def source_commit() -> str:
    """Git SHA of the code that produced this data — stage 1 lineage.

    Returns 'unknown' outside a repo (e.g. running from a ConfigMap in a Job),
    which is honest rather than fabricating a value.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return os.environ.get("SOURCE_COMMIT", "unknown")


def money(rng, lo, hi) -> Decimal:
    return Decimal(rng.randrange(lo * 100, hi * 100)) / 100


def main() -> None:
    rng = random.Random(SEED)
    run_id = f"ING-{ANCHOR_DATE:%Y%m%d}-{SEED}"
    start = ANCHOR_DATE - timedelta(days=MONTHS_HISTORY * 30)

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "postgres.nova.svc.cluster.local"),
        dbname=os.environ.get("PGDATABASE", "nova"),
        user=os.environ.get("PGUSER", "nova"),
        password=os.environ["PGPASSWORD"],
    )
    conn.autocommit = False
    cur = conn.cursor()

    # Idempotent: a re-run replaces the dataset rather than doubling it. Order
    # matters — children before parents, because of the FK constraints.
    cur.execute("TRUNCATE transactions, cards, loans, accounts, customers, ingest_runs CASCADE")

    cur.execute(
        "INSERT INTO ingest_runs (ingest_run_id, source_commit) VALUES (%s, %s)",
        (run_id, source_commit()),
    )

    # ---- customers -------------------------------------------------------
    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        cid = f"CUS-{i:05d}"
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        customers.append((
            cid,
            name,
            f"{name.split()[0].lower()}.{i}@example.ae",
            f"+9715{rng.randrange(10_000_000, 99_999_999)}",
            rng.choice(SEGMENTS),
            start - timedelta(days=rng.randrange(0, 2_000)),
            run_id,
        ))
    cur.executemany(
        "INSERT INTO customers (customer_id, full_name, email, phone, segment, joined_at,"
        " ingest_run_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        customers,
    )

    # ---- accounts --------------------------------------------------------
    # Balance is a placeholder here; recomputed from transactions at the end.
    accounts = []
    for i in range(1, N_ACCOUNTS + 1):
        acct_type = rng.choice(ACCOUNT_TYPES)
        accounts.append((
            f"ACC-{i:05d}",
            f"CUS-{rng.randrange(1, N_CUSTOMERS + 1):05d}",
            acct_type,
            "AED",
            Decimal(0),
            money(rng, 1_000, 20_000) if acct_type == "current" else Decimal(0),
            "active" if rng.random() > 0.06 else rng.choice(["dormant", "frozen"]),
            start - timedelta(days=rng.randrange(0, 1_500)),
            run_id,
        ))
    cur.executemany(
        "INSERT INTO accounts (account_id, customer_id, account_type, currency, balance,"
        " overdraft_limit, status, opened_at, ingest_run_id)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        accounts,
    )

    # ---- transactions ----------------------------------------------------
    # COPY rather than INSERT: 200k rows one at a time takes minutes, COPY takes
    # seconds. Built in memory as TSV, then streamed in one call.
    buf = io.StringIO()
    for i in range(1, N_TRANSACTIONS + 1):
        acct = f"ACC-{rng.randrange(1, N_ACCOUNTS + 1):05d}"
        category, direction, lo, hi, monthly = rng.choice(SPEND)
        offset = rng.randrange(0, MONTHS_HISTORY * 30)
        txn_date = ANCHOR_DATE - timedelta(days=offset)
        if monthly:
            # Recurring items land on a stable day of the month, so "did my salary
            # land this month" has a findable answer rather than a random one.
            txn_date = txn_date.replace(day=25 if category == "salary" else 1)
        buf.write("\t".join([
            f"TXN-{i:07d}", acct, txn_date.isoformat(), str(money(rng, lo, hi)),
            direction, category,
            f"{category.replace('_', ' ').title()} payment",
            rng.choice(["Emirates NBD", "ADCB", "Carrefour", "DEWA", "Talabat", "\\N"]),
            run_id,
        ]) + "\n")
    buf.seek(0)
    cur.copy_from(
        buf, "transactions",
        columns=("txn_id", "account_id", "txn_date", "amount", "direction",
                 "category", "description", "counterparty", "ingest_run_id"),
    )

    # ---- cards -----------------------------------------------------------
    cards = []
    for i, (acct_id, *_rest) in enumerate(accounts, start=1):
        if rng.random() > 0.82:      # not every account has a card
            continue
        card_type = rng.choice(["debit", "debit", "credit"])
        cards.append((
            f"CRD-{i:05d}", acct_id, card_type,
            f"{rng.randrange(1000, 9999)}",
            "active" if rng.random() > 0.1 else rng.choice(["blocked", "expired"]),
            ANCHOR_DATE + timedelta(days=rng.randrange(30, 1_400)),
            money(rng, 5_000, 100_000) if card_type == "credit" else None,
            run_id,
        ))
    cur.executemany(
        "INSERT INTO cards (card_id, account_id, card_type, last_four, status, expiry,"
        " credit_limit, ingest_run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        cards,
    )

    # ---- loans -----------------------------------------------------------
    loans = []
    for i in range(1, N_CUSTOMERS + 1):
        if rng.random() > 0.3:
            continue
        principal = money(rng, 20_000, 900_000)
        loans.append((
            f"LON-{i:05d}", f"CUS-{i:05d}",
            rng.choice(["personal", "auto", "mortgage"]),
            principal,
            principal * Decimal(rng.randrange(10, 95)) / 100,   # outstanding < principal
            Decimal(rng.randrange(2_500, 9_500)) / 1000,        # 2.5% – 9.5%
            rng.choice([12, 24, 36, 60, 120, 240]),
            start - timedelta(days=rng.randrange(0, 1_200)),
            run_id,
        ))
    cur.executemany(
        "INSERT INTO loans (loan_id, customer_id, loan_type, principal, outstanding,"
        " annual_rate, term_months, start_date, ingest_run_id)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        loans,
    )

    # ---- reconcile balances ---------------------------------------------
    # The whole point: balance must equal what the transactions say it is, or the
    # agent can answer two questions and contradict itself.
    cur.execute("""
        UPDATE accounts a SET balance = COALESCE(t.net, 0)
        FROM (
            SELECT account_id,
                   SUM(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) AS net
            FROM transactions GROUP BY account_id
        ) t
        WHERE a.account_id = t.account_id
    """)

    counts = {}
    for table in ("customers", "accounts", "transactions", "cards", "loans"):
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]

    cur.execute(
        "UPDATE ingest_runs SET finished_at = now(), row_counts = %s WHERE ingest_run_id = %s",
        (psycopg2.extras.Json(counts) if hasattr(psycopg2, "extras") else str(counts), run_id),
    )

    conn.commit()
    print(f"ingest_run_id={run_id}")
    for table, n in counts.items():
        print(f"  {table}: {n:,}")


if __name__ == "__main__":
    import psycopg2.extras  # noqa: F401  — registers the Json adapter used above
    main()
