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

-- 1. An account with MORE THAN ONE card, so the answer has to enumerate rather than
--    report a single row. Preferring an account with both a debit and a credit card
--    makes the case harder in the way that matters: credit_limit is NULL on debit
--    cards, and an agent that flattens the two card types will say something false
--    about one of them.
SELECT c.account_id,
       COUNT(*)                              AS card_count,
       COUNT(DISTINCT c.card_type)           AS distinct_types,
       STRING_AGG(c.card_type || '/' || c.status, ', ' ORDER BY c.card_id) AS cards
FROM cards c
GROUP BY c.account_id
HAVING COUNT(DISTINCT c.card_type) > 1
ORDER BY card_count DESC, c.account_id
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
