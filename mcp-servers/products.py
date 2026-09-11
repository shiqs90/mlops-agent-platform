"""mcp-products — cards and loans.

The smallest server, and deliberately so: it exists to make routing a real decision.
With only accounts and transactions, tool selection is close to a coin flip the model
can't get wrong. A third domain means `tool_correctness` measures something.
"""

import os

from mcp.server.fastmcp import FastMCP

from common import cursor, jsonable

mcp = FastMCP("products", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


@mcp.tool()
def get_cards(account_id: str) -> dict:
    """List the cards issued against an account, with status and credit limits.

    Call this for anything about a physical or virtual card — "is my card active",
    "when does it expire", "what's my credit limit", "why was my card declined".
    Do not use it for balances; a credit limit and an account balance are different
    numbers and confusing them produces a confidently wrong answer.

    Args:
        account_id: Account identifier, e.g. ACC-02291.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT c.card_id, c.card_type, c.last_four, c.status, c.expiry,"
            " c.credit_limit, a.currency"
            " FROM cards c JOIN accounts a ON a.account_id = c.account_id"
            " WHERE c.account_id = %s ORDER BY c.card_type",
            (account_id,),
        )
        rows = jsonable(cur.fetchall())
    return {"account_id": account_id, "count": len(rows), "cards": rows}


@mcp.tool()
def get_loans(customer_id: str) -> dict:
    """List a customer's loans — type, principal, outstanding balance, rate, term.

    Call this for questions about borrowing: "how much do I still owe", "what's my
    interest rate", "when does my mortgage end". Loans belong to a customer, not an
    account, so this takes a customer_id — use list_accounts if you only have an
    account and need to find the owner.

    Args:
        customer_id: Customer identifier, e.g. CUS-00412.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT loan_id, loan_type, principal, outstanding, annual_rate,"
            " term_months, start_date FROM loans WHERE customer_id = %s"
            " ORDER BY start_date DESC",
            (customer_id,),
        )
        rows = jsonable(cur.fetchall())

        # `loans` has no currency column and no account_id, so it is inferred from the
        # customer's accounts — null when they disagree, rather than guessing.
        cur.execute(
            "SELECT DISTINCT currency FROM accounts WHERE customer_id = %s", (customer_id,)
        )
        currencies = [r["currency"] for r in cur.fetchall()]

    return {"customer_id": customer_id, "count": len(rows),
            "currency": currencies[0] if len(currencies) == 1 else None,
            "total_outstanding": round(sum(r["outstanding"] for r in rows), 2),
            "loans": rows}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
