-- Nova banking dataset.
--
-- Static by design: the golden set's expected answers are computed against this
-- data with SQL, so the data must not move underneath them. Nothing here is
-- generated at query time.
--
-- Every row carries an ingest_run_id. That is stage 1 of the five-stage debugging
-- path: when an answer turns out to be based on stale or wrong data, the question
-- "which load put it there?" has to be answerable without guessing.

-- ---------------------------------------------------------------------------
-- Lineage
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_runs (
    ingest_run_id   TEXT PRIMARY KEY,
    source_commit   TEXT        NOT NULL,   -- git SHA of the seed script that ran
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    row_counts      JSONB                   -- {"customers": 2000, "accounts": 3000, ...}
);

-- ---------------------------------------------------------------------------
-- Core banking entities
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    customer_id     TEXT PRIMARY KEY,       -- CUS-00001
    full_name       TEXT        NOT NULL,
    email           TEXT        NOT NULL,
    phone           TEXT        NOT NULL,
    segment         TEXT        NOT NULL,   -- retail | premier | private
    joined_at       DATE        NOT NULL,
    ingest_run_id   TEXT        NOT NULL REFERENCES ingest_runs(ingest_run_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,       -- ACC-00001
    customer_id     TEXT        NOT NULL REFERENCES customers(customer_id),
    account_type    TEXT        NOT NULL,   -- current | savings | fixed_deposit
    currency        CHAR(3)     NOT NULL DEFAULT 'AED',
    balance         NUMERIC(14,2) NOT NULL,
    overdraft_limit NUMERIC(14,2) NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL,   -- active | dormant | frozen
    opened_at       DATE        NOT NULL,
    ingest_run_id   TEXT        NOT NULL REFERENCES ingest_runs(ingest_run_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    txn_id          TEXT PRIMARY KEY,       -- TXN-0000001
    account_id      TEXT        NOT NULL REFERENCES accounts(account_id),
    txn_date        DATE        NOT NULL,
    amount          NUMERIC(14,2) NOT NULL, -- always positive; direction carries the sign
    direction       TEXT        NOT NULL,   -- credit | debit
    category        TEXT        NOT NULL,   -- salary | groceries | rent | transfer | ...
    description     TEXT        NOT NULL,
    counterparty    TEXT,
    ingest_run_id   TEXT        NOT NULL REFERENCES ingest_runs(ingest_run_id)
);

CREATE TABLE IF NOT EXISTS cards (
    card_id         TEXT PRIMARY KEY,       -- CRD-00001
    account_id      TEXT        NOT NULL REFERENCES accounts(account_id),
    card_type       TEXT        NOT NULL,   -- debit | credit
    last_four       CHAR(4)     NOT NULL,
    status          TEXT        NOT NULL,   -- active | blocked | expired
    expiry          DATE        NOT NULL,
    credit_limit    NUMERIC(14,2),          -- NULL for debit cards
    ingest_run_id   TEXT        NOT NULL REFERENCES ingest_runs(ingest_run_id)
);

CREATE TABLE IF NOT EXISTS loans (
    loan_id         TEXT PRIMARY KEY,       -- LON-00001
    customer_id     TEXT        NOT NULL REFERENCES customers(customer_id),
    loan_type       TEXT        NOT NULL,   -- personal | auto | mortgage
    principal       NUMERIC(14,2) NOT NULL,
    outstanding     NUMERIC(14,2) NOT NULL,
    annual_rate     NUMERIC(5,3)  NOT NULL, -- 4.250 = 4.25%
    term_months     INT         NOT NULL,
    start_date      DATE        NOT NULL,
    ingest_run_id   TEXT        NOT NULL REFERENCES ingest_runs(ingest_run_id)
);

-- ---------------------------------------------------------------------------
-- Indexes
--
-- Sized for the query shapes the MCP connectors actually issue, not speculatively:
-- transactions are always filtered by account and date range; accounts are always
-- looked up by customer.
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_accounts_customer      ON accounts(customer_id);
CREATE INDEX IF NOT EXISTS idx_txn_account_date       ON transactions(account_id, txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_txn_account_category   ON transactions(account_id, category);
CREATE INDEX IF NOT EXISTS idx_cards_account          ON cards(account_id);
CREATE INDEX IF NOT EXISTS idx_loans_customer         ON loans(customer_id);
