-- Resolve the two placeholders in questions.yaml against the live seed.
--
-- WHY THIS FILE EXISTS RATHER THAN HARDCODED IDS: the golden set is pinned to seed
-- ING-20260801-42, and two cases need values that only the database knows — an
-- account that actually has cards, and a transaction description that actually
-- occurs. Guessing them produces a case that passes for the wrong reason (empty
-- result, trivially faithful), which is exactly the defect gs-002 had.
--
-- Run after a reseed, or before the first scored run. Then edit questions.yaml:
--   gs-014  SEARCH_TERM     -> the term from query 2
--   gs-015  ACC-WITH-CARDS  -> the account_id from query 1
--
--   kubectl exec -n nova -it postgres-0 -- psql -U nova -d nova \
--     -f /dev/stdin < eval/golden/README-placeholders.sql
--
-- (If the heredoc form is awkward, paste the two queries into `psql` directly —
-- they are short on purpose.)

-- 1. An account with an ACTIVE CREDIT card.
--
--    The first version of this query asked for an account holding more than one card
--    TYPE and returned zero rows: db/seed.py issues at most one card per account
--    (2,488 cards across 3,000 accounts), so the filter could never match. Corrected
--    to ask for the strongest case the data can actually supply.
--
--    Credit rather than debit on purpose: credit_limit is NULL on debit cards, so a
--    credit card gives the agent one more real field to misreport. Active rather than
--    blocked/expired so the answer is unambiguous — "you have a card" and "you have a
--    card that works" are different claims, and status is exactly the kind of field an
--    agent skips.
--    Ordered by preference rather than FILTERED by it, so this can never come back
--    empty the way the first version did. Best candidates float to the top; take row 1.
SELECT account_id, card_id, card_type, last_four, status, expiry, credit_limit
FROM cards
ORDER BY (card_type = 'credit' AND status = 'active') DESC,
         (status = 'active') DESC,
         account_id
LIMIT 5;

-- 2. A description that occurs on ACC-00004 and is distinctive enough to search for.
--    Ordered by how OFTEN it appears: a term matching several rows tests that the
--    agent reports a list, while a term matching exactly one row lets a lazy answer
--    look correct. Pick a word from `description`, not the whole string — the case is
--    testing free-text extraction from a sentence, and nobody searches in full
--    sentences.
SELECT description, counterparty, COUNT(*) AS hits
FROM transactions
WHERE account_id = 'ACC-00004'
GROUP BY description, counterparty
HAVING COUNT(*) BETWEEN 2 AND 8
ORDER BY hits DESC
LIMIT 10;

-- 3. Sanity check for the pinned facts in the questions.yaml header. If these drift,
--    the expect_answer values on gs-001 and gs-008 are stale and correctness will
--    fail for a reason that has nothing to do with the agent.
SELECT a.account_id, a.account_type, a.balance, a.overdraft_limit,
       (SELECT COUNT(*) FROM cards WHERE account_id = a.account_id) AS cards
FROM accounts a
WHERE a.account_id IN ('ACC-00001', 'ACC-00003', 'ACC-00004')
ORDER BY a.account_id;
