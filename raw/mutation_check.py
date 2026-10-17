"""Mutation check for spendguard/guard.py (2026-10-10). Run from a COPY of the repo root:
   python3 raw/mutation_check.py
Disables one guard rule at a time, runs the unit tests, and restores the file."""
import subprocess, re
P = 'spendguard/guard.py'
src = open(P).read()
MUTS = {
 'halted check': ('        if self.halted:\n', '        if False:\n'),
 'currency': ('        if p.currency != cur:\n', '        if False:\n'),
 'merchant allow list': ('        if pol.allowed_merchants and p.merchant not in pol.allowed_merchants:\n', '        if False:\n'),
 'per-order limit': ('        if total > pol.per_order_limit:\n', '        if False:\n'),
 'daily limit': ('        if today + total > pol.daily_limit:\n', '        if False:\n'),
 'per-merchant daily limit': ('        if at_m + total > pol.per_merchant_daily_limit:\n', '        if False:\n'),
 'auto-approve threshold': ('        if total > pol.approval_threshold:\n', '        if False:\n'),
 'price drift': ('            if drift > pol.max_price_drift_pct:\n', '            if False:\n'),
 'approval may clear hard rules': ('            if approved and not r.hard:\n', '            if approved:\n'),
}
try:
    for k, (a, b) in MUTS.items():
        assert src.count(a) == 1, k
        open(P, 'w').write(src.replace(a, b))
        r = subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'], capture_output=True, text=True)
        print(f'{k}: {r.stderr.strip().splitlines()[-1]}')
finally:
    open(P, 'w').write(src)
