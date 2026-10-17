import unittest

from spendguard import compare, paypal
from spendguard.agent import Shopper
from spendguard.guard import Guard
from spendguard.policy import Policy


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.rows = {r["key"]: r for r in compare.run_all()}

    def test_seven_scenarios_both_sides(self):
        self.assertEqual(len(self.rows), 7)

    def test_guard_on_blocks_what_guard_off_sends(self):
        for k in ("too_big", "injection", "approval", "surge"):
            self.assertEqual(self.rows[k]["on"]["sent_requests"], 0, k)
            self.assertEqual(self.rows[k]["off"]["sent_requests"], 1, k)
        self.assertEqual(self.rows["injection"]["off"]["sent_cents"], 2 * 189900)
        self.assertEqual(self.rows["too_big"]["off"]["sent_cents"], 189900)

    def test_normal_purchase_is_identical(self):
        self.assertEqual(self.rows["normal"]["off"], self.rows["normal"]["on"])

    def test_daily_limit_and_breaker(self):
        self.assertEqual((self.rows["daily"]["off"]["sent_requests"], self.rows["daily"]["on"]["sent_requests"]), (3, 2))
        self.assertEqual((self.rows["breaker"]["off"]["sent_requests"], self.rows["breaker"]["on"]["sent_requests"]), (3, 2))
        self.assertTrue(self.rows["breaker"]["on"]["halted"])
        self.assertFalse(self.rows["breaker"]["off"]["halted"])

    def test_totals(self):
        t = compare.totals(list(self.rows.values()))
        self.assertEqual((t["off"]["sent_requests"], t["on"]["sent_requests"]), (11, 5))
        self.assertEqual(t["on"]["sent_cents"], 7797)

    def test_unguarded_guard_is_not_used_by_default_app(self):
        from spendguard.server import App
        a = App({"PAYPAL_MODE": "replay"})
        self.assertIs(type(a.shopper.guard), Guard)
        self.assertEqual(a.shopper.paypal.mode, "replay")

    def test_compare_page_is_served_and_labelled_replay(self):
        html = compare.render_html(compare.run_all())
        self.assertIn("Replay numbers", html)
        self.assertIn("5849.05 USD", html)

    def test_guard_off_really_ignores_limits(self):
        pp = paypal.ReplayPayPal()
        s = Shopper(compare.UnguardedGuard(Policy()), pp)
        self.assertEqual(s.purchase("gpu-pro", 1)["status"], "order_created")
        self.assertEqual(len(pp.calls), 1)


if __name__ == "__main__":
    unittest.main()
