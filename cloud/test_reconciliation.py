import copy
import unittest
from prepare_verification import reconcile


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.ledger = {'codex_account_id': 'account-a', 'history': [{'retained': True}],
                       'pools': {
                           'codex': {'reset': 2000000000, 'base': 0, 'peak': 19},
                           'anti:gemini models': {'reset': 2000000100, 'base': 0, 'peak': 3},
                           'anti:gemini 4 argon': {'reset': 2000000100, 'base': 0, 'peak': 17}}}
        self.week = {
            'codex': {'reset': 2000100000, 'used': 1},
            'anti:gemini models': {'reset': 2000000100, 'used': 2},
            'anti:gemini 4 argon': {'reset': 2000000100, 'used': 17}}
        self.floors = {'anti:gemini models': 4, 'anti:gemini 4 argon': 17}

    def apply(self, ledger=None, account='account-a', new_reset=2000100000):
        return reconcile(ledger if ledger is not None else self.ledger, self.week,
                         account, 2000000000, new_reset, 35, self.floors)

    def test_confirmed_reset_preserves_all_history_and_other_session_peak(self):
        before = copy.deepcopy(self.ledger)
        result = self.apply()
        self.assertEqual(self.ledger, before)
        self.assertEqual(result['history'][0], {'retained': True})
        self.assertEqual(result['history'][-1]['peak'], 35)
        self.assertEqual(result['history'][-1]['reset'], 2000000000)
        self.assertEqual(result['pools']['codex']['peak'], 1)
        self.assertEqual(result['pools']['codex']['base'], 0)
        self.assertEqual(result['pools']['anti:gemini models']['peak'], 4)
        self.assertEqual(result['pools']['anti:gemini 4 argon']['peak'], 17)
        for key in self.floors:
            self.assertEqual(result['pools'][key]['reset'], before['pools'][key]['reset'])
            self.assertEqual(result['pools'][key]['base'], before['pools'][key]['base'])

    def test_repeat_preparation_does_not_grant_another_period(self):
        first = self.apply()
        second = self.apply(first)
        self.assertEqual(second, first)

    def test_unknown_account_period_or_anti_reset_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'Account mismatch'):
            self.apply(account='account-b')
        with self.assertRaisesRegex(RuntimeError, 'explicitly confirmed'):
            self.apply(new_reset=2000200000)
        self.week['anti:gemini models']['reset'] += 100
        with self.assertRaisesRegex(RuntimeError, 'Anti period changed'):
            self.apply()

    def test_anti_stop_and_unknown_baseline_cannot_be_reconciled_away(self):
        self.ledger['pools']['anti:gemini 4 argon']['peak'] = 23
        with self.assertRaisesRegex(RuntimeError, 'Anti budget stop'):
            self.apply()
        self.ledger['pools']['anti:gemini 4 argon']['peak'] = 17
        self.ledger['pools']['anti:gemini models']['base'] = 1
        with self.assertRaisesRegex(RuntimeError, 'Unknown Anti baseline'):
            self.apply()

    def test_new_period_without_matching_confirmation_is_rejected(self):
        self.ledger['pools']['codex']['reset'] = 2000100000
        with self.assertRaisesRegex(RuntimeError, 'Ledger period'):
            self.apply()


if __name__ == '__main__':
    unittest.main()
