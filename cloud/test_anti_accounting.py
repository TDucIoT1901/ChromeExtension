import json
from pathlib import Path
import tempfile
import unittest

from runner import Budget, POOLS


class AntiAccountingTests(unittest.TestCase):
    def setUp(self):
        state = Path(__file__).resolve().parent.parent / '.anti-codex'
        state.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=state)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'budget.json'
        self.week = {key: {'used': 4, 'reset': 2000000000} for key in POOLS}
        self.short = {key: {'used': 0} for key in POOLS}
        self.key = 'anti:gemini models'

    def update(self, used):
        self.week[self.key]['used'] = used
        return Budget(self.path).update(self.week, self.short, 1900000000,
                                        account_id='fixture-account')['pools'][self.key]

    def test_drop_never_refunds_and_subsequent_rise_is_charged_after_restart(self):
        self.update(4)
        fallen = self.update(2)
        self.assertEqual(fallen['peak'], 4)
        self.assertEqual(fallen['charged_used'], 4)
        risen = self.update(3)
        self.assertEqual(risen['peak'], 4)
        self.assertEqual(risen['charged_used'], 5)
        self.assertEqual(risen['reset'], 2000000000)
        self.assertEqual(risen['base'], 0)
        self.update(2)
        again = self.update(3)
        self.assertEqual(again['charged_used'], 6)
        self.assertEqual(again['display_decreases'], 2)

    def test_repeated_jitter_can_only_stop_earlier_than_provider_peak(self):
        self.update(19)
        self.update(18)
        self.update(19)  # charged 20, observed peak 19
        with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop'):
            self.update(22)  # charged 23; provider display remains below 23
        saved = json.loads(self.path.read_text())['pools'][self.key]
        self.assertEqual(saved['charged_used'], 23)
        self.assertEqual(saved['peak'], 22)

    def test_same_display_does_not_keep_charging(self):
        self.update(4)
        self.update(2)
        for _ in range(3):
            self.assertEqual(self.update(2)['charged_used'], 4)

    def test_anti_period_change_still_fails_closed(self):
        self.update(4)
        self.week[self.key]['reset'] += 100
        with self.assertRaisesRegex(RuntimeError, 'Reset timestamp drift'):
            self.update(2)


if __name__ == '__main__':
    unittest.main()
