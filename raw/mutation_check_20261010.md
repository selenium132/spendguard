# Mutation check of the guard rules (2026-10-10 00:42 JST)

Why: the submitted text says "I broke four guard rules one at a time and the suite failed each time", but the only record (sandbox_run.md section 5) is one rule (per-order check) broken, 4 tests failed. So the claim was re-run today on a copy of the repo (scratchpad), with `raw/mutation_check.py`. Each rule's `if` was replaced by `if False:` (or the approval re-check by `if approved:`), the 74-test suite was run, the file was restored.

| Rule disabled | Result of the suite |
|---|---|
| halted check | FAILED (failures=4) |
| currency | FAILED (failures=1) |
| merchant allow list | FAILED (failures=1) |
| per-order limit | FAILED (failures=4) |
| daily limit | FAILED (failures=5) |
| per-merchant daily limit | FAILED (failures=1) |
| auto-approve threshold | FAILED (failures=7, errors=1) |
| price drift | FAILED (failures=4) |
| approval may clear hard rules | FAILED (failures=4) |
| restored file | OK (74 tests, skipped=3) |

9 of 9 disabled rules were caught by at least one test. Rules with only 1 failing test (currency, allow list, per-merchant daily) are the thinnest.
