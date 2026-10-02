import threading
import unittest

from spendguard.guard import ALLOW, DENY, HALT, NEEDS_APPROVAL, Guard, Proposal
from spendguard.ledger import Ledger
from spendguard.policy import Policy, to_cents


def prop(total=1000, qty=1, merchant="m1", quoted=None, currency="USD"):
    unit = total // qty
    return Proposal(merchant, "x", qty, unit if quoted is None else quoted, unit, currency)


class PolicyTests(unittest.TestCase):
    def test_to_cents(self):
        self.assertEqual(to_cents("12.34"), 1234)
        self.assertEqual(to_cents(0.1 + 0.2), 30)
        for bad in ("-1", "nan", "abc"):
            with self.assertRaises(Exception):
                to_cents(bad)

    def test_threshold_cannot_exceed_hard_limit(self):
        with self.assertRaises(ValueError):
            Policy(per_order_limit=1000, approval_threshold=2000).validate()

    def test_rejects_negative_and_non_int(self):
        with self.assertRaises(ValueError):
            Policy(daily_limit=-5).validate()
        with self.assertRaises(ValueError):
            Policy(daily_limit=10.5).validate()


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(Policy(per_order_limit=5000, daily_limit=8000, per_merchant_daily_limit=6000,
                              approval_threshold=2500, max_price_drift_pct=5, max_consecutive_failures=2))

    def test_small_order_allowed(self):
        self.assertEqual(self.g.evaluate(prop(1000)).verdict, ALLOW)

    def test_per_order_limit_breach_denied(self):
        d = self.g.evaluate(prop(5001))
        self.assertEqual(d.verdict, DENY)
        self.assertIn("per_order_limit", [r.code for r in d.reasons])

    def test_exactly_at_limit_is_allowed_boundary(self):
        g = Guard(Policy(per_order_limit=5000, approval_threshold=5000))
        self.assertEqual(g.evaluate(prop(5000)).verdict, ALLOW)
        self.assertEqual(g.evaluate(prop(5001)).verdict, DENY)

    def test_quantity_multiplies_into_the_limit(self):
        # 10 x 6.00 = 60.00 > 50.00 even though each unit is small
        self.assertEqual(self.g.evaluate(prop(6000, qty=10)).verdict, DENY)

    def test_daily_limit_breach_denied_after_spending(self):
        for i in range(3):
            d = self.g.authorize(prop(2500, merchant=f"m{i}"))
            self.assertEqual(d.verdict, ALLOW)
            self.g.ledger.commit(d.reservation)  # 7500 so far
        d = self.g.authorize(prop(1000, merchant="other"))
        self.assertEqual(d.verdict, DENY)
        self.assertIn("daily_limit", [r.code for r in d.reasons])

    def test_per_merchant_limit(self):
        d1 = self.g.authorize(prop(5000))
        self.assertEqual(d1.verdict, NEEDS_APPROVAL)  # above 25 threshold
        g = Guard(Policy(per_order_limit=5000, daily_limit=20000, per_merchant_daily_limit=6000, approval_threshold=5000))
        self.assertEqual(g.authorize(prop(4000)).verdict, ALLOW)
        d = g.authorize(prop(2500))
        self.assertEqual(d.verdict, DENY)
        self.assertIn("merchant_daily_limit", [r.code for r in d.reasons])
        self.assertEqual(g.authorize(prop(2500, merchant="m2")).verdict, ALLOW)

    def test_denied_order_reserves_nothing(self):
        self.g.authorize(prop(9000))
        self.assertEqual(self.g.ledger.spent_today(), 0)

    def test_released_reservation_frees_budget(self):
        d = self.g.authorize(prop(2000))
        self.g.ledger.release(d.reservation)
        self.assertEqual(self.g.ledger.spent_today(), 0)

    def test_day_rollover(self):
        now = [1_700_000_000.0]
        g = Guard(Policy(daily_limit=3000, per_order_limit=3000, approval_threshold=3000), Ledger(clock=lambda: now[0]))
        self.assertEqual(g.authorize(prop(3000)).verdict, ALLOW)
        self.assertEqual(g.authorize(prop(100)).verdict, DENY)
        now[0] += 86400
        self.assertEqual(g.authorize(prop(100)).verdict, ALLOW)

    def test_currency_mismatch_denied(self):
        self.assertEqual(self.g.evaluate(prop(1000, currency="EUR")).verdict, DENY)

    def test_merchant_allow_list(self):
        g = Guard(Policy(allowed_merchants=["good"]))
        self.assertEqual(g.evaluate(prop(1000, merchant="evil")).verdict, DENY)
        self.assertEqual(g.evaluate(prop(1000, merchant="good")).verdict, ALLOW)

    def test_bad_quantities_denied(self):
        for q in (0, -3):
            p = Proposal("m", "x", q, 100, 100)
            self.assertEqual(self.g.evaluate(p).verdict, DENY)


class SoftRuleTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(Policy())

    def test_above_threshold_needs_approval(self):
        d = self.g.evaluate(prop(3000))
        self.assertEqual(d.verdict, NEEDS_APPROVAL)

    def test_approval_clears_soft_rule(self):
        self.assertEqual(self.g.evaluate(prop(3000), approved=True).verdict, ALLOW)

    def test_approval_cannot_lift_hard_limit(self):
        d = self.g.evaluate(prop(9999), approved=True)
        self.assertEqual(d.verdict, DENY)

    def test_price_drift_needs_approval(self):
        d = self.g.evaluate(prop(1050, quoted=1000))   # +5.0% is the limit: allowed
        self.assertEqual(d.verdict, ALLOW)
        d = self.g.evaluate(prop(1060, quoted=1000))   # +6%
        self.assertEqual(d.verdict, NEEDS_APPROVAL)
        self.assertIn("price_drift", [r.code for r in d.reasons])

    def test_price_drop_is_fine(self):
        self.assertEqual(self.g.evaluate(prop(800, quoted=1000)).verdict, ALLOW)

    def test_drift_approval_still_bound_by_hard_limits(self):
        d = self.g.evaluate(prop(5200, quoted=1000), approved=True)
        self.assertEqual(d.verdict, DENY)


class CircuitBreakerTests(unittest.TestCase):
    def test_halts_after_n_failures_and_blocks_everything(self):
        g = Guard(Policy(max_consecutive_failures=2))
        g.record_failure("x")
        self.assertFalse(g.halted)
        g.record_failure("x")
        self.assertTrue(g.halted)
        d = g.authorize(prop(100))
        self.assertEqual(d.verdict, HALT)
        self.assertIsNone(d.reservation)
        self.assertEqual(g.evaluate(prop(100), approved=True).verdict, HALT)  # approval cannot bypass

    def test_success_resets_counter(self):
        g = Guard(Policy(max_consecutive_failures=2))
        g.record_failure("x")
        g.record_success()
        g.record_failure("x")
        self.assertFalse(g.halted)

    def test_human_reset(self):
        g = Guard(Policy(max_consecutive_failures=1))
        g.record_failure("x")
        self.assertTrue(g.halted)
        g.reset()
        self.assertEqual(g.authorize(prop(100)).verdict, ALLOW)


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_calls_cannot_overspend(self):
        g = Guard(Policy(per_order_limit=2000, daily_limit=5000, per_merchant_daily_limit=5000, approval_threshold=2000))
        verdicts = []
        def go():
            verdicts.append(g.authorize(prop(2000, merchant="m")).verdict)
        ts = [threading.Thread(target=go) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(verdicts.count(ALLOW), 2)       # 2 x 20.00 = 40.00 fits in 50.00, a 3rd would not
        self.assertLessEqual(g.ledger.spent_today(), 5000)


if __name__ == "__main__":
    unittest.main()
