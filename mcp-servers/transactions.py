"""mcp-transactions — statement history, search, and spending breakdowns.

The date-handling tool. Most `argument_correctness` failures in evaluation come from
here: the customer says "last month" and the agent has to turn that into a date
range. Every tool echoes the range it actually used back in its response, so a
wrong window is visible in the trace rather than hidden inside a plausible answer.
"""

import os

from mcp.server.fastmcp import FastMCP

from common import cursor, jsonable

mcp = FastMCP("transactions", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

CATEGORIES = ["salary", "rent", "utilities", "groceries", "dining",
              "transport", "shopping", "transfer", "opening_balance"]


@mcp.tool()
def query_transactions(account_id: str, start_date: str, end_date: str,
                       category: str = "", limit: int = 50) -> dict:
    """List transactions for an account within a date range, newest first.

    Call this for statement questions, for "did X arrive", and for any question
    about what was spent or received over a period. Dates must be resolved to
    absolute YYYY-MM-DD before calling — this tool does not interpret "last month".

    Args:
        account_id: Account identifier, e.g. ACC-02291.
        start_date: Inclusive start, YYYY-MM-DD.
        end_date: Inclusive end, YYYY-MM-DD.
        category: Optional filter. One of: salary, rent, utilities, groceries,
            dining, transport, shopping, transfer.
        limit: Maximum rows, default 50.
    """
    if category and category not in CATEGORIES:
        return {"error": f"Unknown category '{category}'", "valid_categories": CATEGORIES}

    sql = ("SELECT txn_id, txn_date, amount, direction, category, description, counterparty"
           " FROM transactions WHERE account_id = %s AND txn_date BETWEEN %s AND %s")
    params: list = [account_id, start_date, end_date]
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY txn_date DESC LIMIT %s"
    params.append(min(limit, 200))

    with cursor() as cur:
        cur.execute(sql, params)
        rows = jsonable(cur.fetchall())

    # Echo the window back so a wrong date range is visible in the trace.
    return {"account_id": account_id, "start_date": start_date, "end_date": end_date,
            "category": category or "all", "count": len(rows), "transactions": rows}


@mcp.tool()
def spending_by_category(account_id: str, start_date: str, end_date: str) -> dict:
    """Total spending per category over a date range, largest first.

    Call this for "where is my money going", "how much did I spend on X", and any
    question about spending patterns. Much cheaper than listing every transaction
    and adding them up — prefer it whenever the customer wants totals rather than
    individual entries. Debits only; salary and other credits are excluded.

    Args:
        account_id: Account identifier, e.g. ACC-02291.
        start_date: Inclusive start, YYYY-MM-DD.
        end_date: Inclusive end, YYYY-MM-DD.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT category, count(*) AS transaction_count, SUM(amount) AS total"
            " FROM transactions"
            " WHERE account_id = %s AND txn_date BETWEEN %s AND %s AND direction = 'debit'"
            " GROUP BY category ORDER BY total DESC",
            (account_id, start_date, end_date),
        )
        rows = jsonable(cur.fetchall())

    return {"account_id": account_id, "start_date": start_date, "end_date": end_date,
            "total_spent": round(sum(r["total"] for r in rows), 2), "by_category": rows}


@mcp.tool()
def find_transactions(account_id: str, search_term: str, limit: int = 20) -> dict:
    """Search an account's transactions by description or counterparty.

    Call this when the customer names a merchant or payee rather than a date range —
    "did I pay DEWA", "find my Carrefour charges". Case-insensitive substring match
    across the whole history.

    Args:
        account_id: Account identifier, e.g. ACC-02291.
        search_term: Merchant or description text to match.
        limit: Maximum rows, default 20.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT txn_id, txn_date, amount, direction, category, description, counterparty"
            " FROM transactions"
            " WHERE account_id = %s AND (description ILIKE %s OR counterparty ILIKE %s)"
            " ORDER BY txn_date DESC LIMIT %s",
            (account_id, f"%{search_term}%", f"%{search_term}%", min(limit, 100)),
        )
        rows = jsonable(cur.fetchall())

    return {"account_id": account_id, "search_term": search_term,
            "count": len(rows), "transactions": rows}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
