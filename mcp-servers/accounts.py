"""mcp-accounts — balances, account details, and the one write path.

Tool descriptions are load-bearing. The agent chooses tools from these strings, and
`tool_selection` in the evaluation scores exactly that choice — so a vague
description shows up as a routing failure, not a documentation problem. Each one
says when to call it, not just what it does.
"""

import os

from mcp.server.fastmcp import FastMCP

from common import cursor, jsonable, one

mcp = FastMCP("accounts", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


@mcp.tool()
def check_balance(account_id: str) -> dict:
    """Get the current balance and overdraft limit for one account.

    Call this whenever the customer asks how much money they have, whether they are
    overdrawn, or how much they can spend. Returns balance, overdraft_limit, and
    available (balance + overdraft_limit) — a negative balance within the overdraft
    limit is normal, not an error.

    Args:
        account_id: Account identifier, e.g. ACC-02291.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT account_id, account_type, currency, balance, overdraft_limit, status"
            " FROM accounts WHERE account_id = %s",
            (account_id,),
        )
        row = one(cur.fetchall())
    if row is None:
        return {"error": f"No account {account_id}"}
    row["available"] = row["balance"] + row["overdraft_limit"]
    row["is_overdrawn"] = row["balance"] < 0
    row["exceeds_overdraft"] = row["balance"] < -row["overdraft_limit"]
    return row


@mcp.tool()
def list_accounts(customer_id: str) -> list[dict]:
    """List every account belonging to one customer.

    Call this when the customer refers to "my accounts", asks about a type of account
    ("my savings"), or when you need an account_id before calling another tool and
    only have a customer_id.

    Args:
        customer_id: Customer identifier, e.g. CUS-00412.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT account_id, account_type, currency, balance, overdraft_limit, status,"
            " opened_at FROM accounts WHERE customer_id = %s ORDER BY opened_at",
            (customer_id,),
        )
        return jsonable(cur.fetchall())


@mcp.tool()
def get_customer(customer_id: str) -> dict:
    """Look up a customer's profile — name, segment, contact details, join date.

    Call this only when the customer's own details are the question. Do not call it
    to find their accounts; use list_accounts for that.

    Args:
        customer_id: Customer identifier, e.g. CUS-00412.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT customer_id, full_name, email, phone, segment, joined_at"
            " FROM customers WHERE customer_id = %s",
            (customer_id,),
        )
        return one(cur.fetchall()) or {"error": f"No customer {customer_id}"}


@mcp.tool()
def initiate_transfer(from_account: str, to_account: str, amount: float,
                      reference: str = "") -> dict:
    """Move money between two accounts. THIS MOVES REAL MONEY.

    The only write tool in the platform. It is the one call the human-approval
    middleware guards — every other tool is read-only, so this is the entire
    destructive surface.

    Rejects the transfer if it would push the source account past its overdraft
    limit. Validation lives here rather than in the prompt: a guardrail the model
    can be talked out of is not a guardrail.

    Args:
        from_account: Source account, e.g. ACC-02291.
        to_account: Destination account.
        amount: Positive amount in AED.
        reference: Optional description shown on both statements.
    """
    if amount <= 0:
        return {"error": "Amount must be positive"}

    with cursor() as cur:
        cur.execute(
            "SELECT balance, overdraft_limit, status FROM accounts WHERE account_id = %s",
            (from_account,),
        )
        src = one(cur.fetchall())
        if src is None:
            return {"error": f"No account {from_account}"}
        if src["status"] != "active":
            return {"error": f"Account {from_account} is {src['status']}"}
        if src["balance"] - amount < -src["overdraft_limit"]:
            return {
                "error": "Transfer would exceed the overdraft limit",
                "balance": src["balance"],
                "overdraft_limit": src["overdraft_limit"],
                "requested": amount,
            }

        cur.execute("SELECT account_id FROM accounts WHERE account_id = %s", (to_account,))
        if not cur.fetchall():
            return {"error": f"No account {to_account}"}

        # Single statement so both legs commit together. A partial transfer is worse
        # than a failed one.
        cur.execute(
            "UPDATE accounts SET balance = balance + CASE account_id"
            "   WHEN %s THEN -%s::numeric ELSE %s::numeric END"
            " WHERE account_id IN (%s, %s)",
            (from_account, amount, amount, from_account, to_account),
        )
        cur.execute(
            "SELECT account_id, balance FROM accounts WHERE account_id IN (%s, %s)",
            (from_account, to_account),
        )
        balances = jsonable(cur.fetchall())

    return {"status": "completed", "amount": amount, "reference": reference,
            "balances": balances}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
