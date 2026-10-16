"""Adversarial, no-VPS tests for the Git-only schedule burst guard."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import zcloud_schedule_burst_budget_20261016 as guard


def yaml(cron):
    return ('name: fixture\non:\n  schedule:\n'
            f'    - cron: "{cron}"\n  workflow_dispatch:\njobs: {{}}\n')


class ScheduleParsingTests(unittest.TestCase):
    def test_minutes_ranges_steps_and_weekdays(self):
        minute, hour, day, month, dow = guard.fields('*/15 9-17/2 * * 1,3,7')
        self.assertEqual(minute, {0, 15, 30, 45})
        self.assertEqual(hour, {9, 11, 13, 15, 17})
        self.assertEqual(day, set(range(1, 32)))
        self.assertEqual(month, set(range(1, 13)))
        self.assertEqual(dow, {0, 1, 3})

    def test_reject_ambiguous_crons(self):
        for expr in ('* * * *', '@daily', '*/0 * * * *',
                     '5-2 * * * *', '60 * * * *', '${{ inputs.cron }}',
                     '1,,2 * * * *', '* */x * * *', '1/2/3 * * * *'):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    guard.fields(expr)

    def test_quoted_and_unquoted_cron(self):
        self.assertEqual(guard.schedules(yaml('8 */2 * * *')), ['8 */2 * * *'])
        self.assertEqual(guard.schedules(
            "on:\n  schedule:\n    - cron: '0 1 * * *' # comment\n"
        ), ['0 1 * * *'])

    def test_nested_fake_cron_is_not_event(self):
        source = ('name: decoy\nenv:\n  schedule:\n    - cron: "0 * * * *"\n'
                  'on:\n  workflow_dispatch:\njobs:\n  fixture:\n    '
                  'steps:\n      - run: echo "schedule:"\n')
        self.assertEqual(guard.schedules(source), [])

    def test_ambiguous_yaml_fails_closed(self):
        for source in (
            'on: {schedule: [{cron: "* * * * *"}]}\n',
            'on:\n  schedule: [{cron: "* * * * *"}]\n',
            'on:\n  schedule:\n    - id: malicious\n',
            'on:\n  schedule:\n    - cron: "0 * * * *"\n'
            '    - cron: "0 * * * *"\non:\n  workflow_dispatch:\n',
            "on:\n  schedule:\n    - cron: ${{ inputs.cron }}\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    guard.schedules(source)

    def test_conservative_month_and_day_of_month(self):
        self.assertEqual(len(guard.weekly_slots('0 3 1 2 *')), 7)
        self.assertEqual(len(guard.weekly_slots('0 3 * * 1')), 1)


class BurstBudgetTests(unittest.TestCase):
    def test_grandfathers_existing_peak_but_blocks_growth(self):
        baseline = {f'w{i}.yml': ['0 * * * *'] for i in range(7)}
        report = guard.compare(baseline, baseline)
        self.assertEqual(report['baseline_peak'], 7)
        self.assertEqual(report['new_over_budget_slots'], 0)
        extra = {**baseline, 'eighth.yml': ['0 * * * *']}
        self.assertEqual(guard.compare(baseline, extra)['new_over_budget_slots'], 24 * 7)
        self.assertFalse(guard.compare(baseline, extra)['deploy_authorized'])
        self.assertFalse(guard.compare(baseline, extra)['mutation_performed'])

    def test_new_conflicting_minute_fails_but_spread_passes(self):
        baseline = {f'w{i}.yml': ['0 * * * *'] for i in range(4)}
        new = {**baseline, 'w4.yml': ['0 * * * *']}
        spread = {**baseline, 'w4.yml': ['1 * * * *']}
        self.assertGreater(guard.compare(baseline, new)['new_over_budget_slots'], 0)
        self.assertEqual(guard.compare(baseline, spread)['new_over_budget_slots'], 0)

    def test_deleting_or_shifting_event_to_less_busy_slot_passes(self):
        baseline = {f'w{i}.yml': ['0 * * * *'] for i in range(6)}
        candidate = dict(baseline)
        del candidate['w0.yml']
        candidate['w5.yml'] = ['1 * * * *']
        self.assertEqual(guard.compare(baseline, candidate)['new_over_budget_slots'], 0)

    def test_weekday_only_schedule_does_not_raise_unrelated_weekend(self):
        baseline = {f'w{i}.yml': ['0 12 * * 1'] for i in range(4)}
        report = guard.compare(baseline, {**baseline, 'new.yml': ['0 12 * * 2']})
        self.assertEqual(report['new_over_budget_slots'], 0)


class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '.github' / 'workflows'
        self.path.mkdir(parents=True)
        self.shell('init', '-q')
        self.shell('config', 'user.email', 'ci@example.test')
        self.shell('config', 'user.name', 'CI Fixture')
        original = guard.git
        def fixture_git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args],
                                  check=True, capture_output=True, timeout=10).stdout
        guard.git = fixture_git
        self.addCleanup(setattr, guard, 'git', original)

    def shell(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args]).decode().strip()

    def write(self, filename, data):
        (self.path / filename).write_text(data, encoding='utf-8')
        self.shell('add', '-A')
        self.shell('commit', '-qm', 'fixture')
        return self.shell('rev-parse', 'HEAD')

    def test_exact_git_objects_detect_new_burst(self):
        for index in range(4):
            self.write(f'a{index}.yml', yaml('0 * * * *'))
        before = self.shell('rev-parse', 'HEAD')
        after = self.write('extra.yml', yaml('0 * * * *'))
        candidate = guard.read_tree(after)
        self.assertEqual(len(candidate), 5)
        self.assertGreater(
            guard.compare(guard.read_tree(before), candidate)['new_over_budget_slots'], 0)
        self.assertEqual(len(guard.read_tree(before)), 4)

    def test_workflow_symlink_rejected(self):
        self.write('seed.yml', yaml('2 * * * *'))
        (self.path / 'link.yml').symlink_to('seed.yml')
        self.shell('add', '-A')
        self.shell('commit', '-qm', 'symlink fixture')
        with self.assertRaisesRegex(ValueError, 'git mode'):
            guard.read_tree(self.shell('rev-parse', 'HEAD'))

    def test_unknown_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, 'immutable'):
            guard.read_tree('main')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            guard.read_tree('0' * 41)

    def test_candidate_source_bad_cron_rejected(self):
        sha = self.write('bad.yml', yaml('abc * * * *'))
        with self.assertRaises(ValueError):
            guard.read_tree(sha)


if __name__ == '__main__':
    unittest.main()
