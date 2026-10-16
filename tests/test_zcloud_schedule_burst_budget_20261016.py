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

    def test_oversized_single_cron_list_fails_before_expansion(self):
        # Large comma-lists can be within the YAML blob budget but force
        # expensive repeated tokenization if a single entry is unbounded.
        adversarial = "0 9 * * " + ",".join(["1"] * 300)
        self.assertGreater(len(adversarial), guard.MAX_CRON_CHARACTERS)
        with self.assertRaisesRegex(ValueError, "bounded lexical budget"):
            guard.fields(adversarial)
        with self.assertRaisesRegex(ValueError, "bounded lexical budget"):
            guard.schedules(yaml(adversarial))

    def test_normal_cron_lists_below_character_budget_are_supported(self):
        moderate = "5,10,15,20,25,30,35,40,45,50,55 * * * 1,2,3,4,5"
        self.assertLess(len(moderate), guard.MAX_CRON_CHARACTERS)
        self.assertEqual(len(guard.fields(moderate)[0]), 11)

    def test_quoted_and_unquoted_cron(self):
        self.assertEqual(guard.schedules(yaml('8 */2 * * *')), ['8 */2 * * *'])
        self.assertEqual(guard.schedules(
            "on:\n  schedule:\n    - cron: '0 1 * * *' # comment\n"
        ), ['0 1 * * *'])

    def test_arbitrary_consistent_yaml_indentation_is_counted(self):
        for trigger_spaces, item_spaces in (
            (1, 3), (2, 4), (4, 6), (4, 7), (6, 10),
        ):
            source = (
                'on:\n'
                + ' ' * trigger_spaces + '"schedule":\n'
                + ' ' * item_spaces + '- "cron": "0 9 * * 1"\n'
            )
            with self.subTest(trigger_indent=trigger_spaces,
                              item_indent=item_spaces):
                self.assertEqual(guard.schedules(source), ['0 9 * * 1'])

    def test_alternate_indent_with_previous_nested_event(self):
        source = (
            'on:\n'
            '    workflow_run:\n'
            '       workflows: ["regression smoke"]\n'
            '       types: [completed]\n'
            '    schedule:\n'
            '       - cron: "12 18 * * 3"\n'
        )
        self.assertEqual(guard.schedules(source), ['12 18 * * 3'])

    def test_deceptive_nested_schedule_under_unrelated_event_ignored(self):
        source = (
            'on:\n'
            '    push:\n'
            '      branches:\n'
            '        schedule:\n'
            '          - cron: "0 1 * * *"\n'
            '    workflow_dispatch:\n'
        )
        self.assertEqual(guard.schedules(source), [])

    def test_mixed_trigger_or_schedule_indent_fails_closed(self):
        for source, reason in (
            ('on:\n    push:\n  schedule:\n    - cron: "0 9 * * 1"\n',
             'inconsistent on trigger'),
            ('on:\n  schedule:\n    - cron: "0 9 * * 1"\n'
             '     - cron: "0 10 * * 1"\n',
             'inconsistent schedule entry'),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, reason):
                    guard.schedules(source)

    def test_alternate_indent_quoted_event_denies_collision_growth(self):
        base = {f'w{i}.yml': ['0 9 * * 1'] for i in range(4)}
        source = 'on:\n    "schedule":\n      - "cron": "0 9 * * 1"\n'
        candidate = {**base, 'new.yml': guard.schedules(source)}
        self.assertEqual(guard.compare(base, candidate)['new_over_budget_slots'], 1)

    def test_quoted_schedule_and_cron_keys_are_counted(self):
        forms = (
            ('on:\n  "schedule":\n    - "cron": "0 9 * * 1"\n', "0 9 * * 1"),
            ("on:\n  'schedule':\n    - 'cron': '12 2 * * 4'\n", "12 2 * * 4"),
            ('"on":\n  "schedule":\n    - cron: "31 3 * * 5"\n', "31 3 * * 5"),
            ("'on':\n  schedule:\n    - 'cron': '12 2 * * 4'\n", "12 2 * * 4"),
        )
        for source, expected in forms:
            with self.subTest(source=source):
                self.assertEqual(guard.schedules(source), [expected])

    def test_quoted_schedule_keys_cannot_hide_burst_growth(self):
        base = {f'w{i}.yml': ['0 9 * * 1'] for i in range(4)}
        added = guard.schedules('on:\n  "schedule":\n    - "cron": "0 9 * * 1"\n')
        self.assertEqual(
            guard.compare(base, {**base, 'new.yml': added})['new_over_budget_slots'],
            1,
        )

    def test_duplicate_quoted_schedule_keys_fail_closed(self):
        source = (
            'on:\n  schedule:\n    - cron: "0 3 * * *"\n'
            '  "schedule":\n    - "cron": "0 4 * * *"\n'
        )
        with self.assertRaisesRegex(ValueError, "duplicate schedule"):
            guard.schedules(source)

    def test_escaped_quoted_keys_rejected(self):
        for source in (
            r'"o\u006e":' + "\n" + '  schedule:\n    - cron: "0 9 * * 1"\n',
            r'on:' + "\n" + r'  "s\u0063hedule":' + "\n" + '    - cron: "0 9 * * 1"\n',
            r'on:' + "\n" + '  schedule:\n' + r'    - "cr\u006fn": "0 9 * * 1"' + "\n",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "escaped YAML"):
                    guard.schedules(source)

    def test_inline_quoted_schedule_remains_denied(self):
        for key in ('schedule', "'schedule'", '"schedule"'):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "inline schedule"):
                    guard.schedules('on:\n  ' + key + ': [{cron: "0 9 * * 1"}]\n')

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
            'on:\n  <<: *scheduled\n  workflow_dispatch:\n',
            'defaults: &scheduled\n  on:\n    schedule:\n'
            '      - cron: "0 * * * *"\n<<: *scheduled\n',
        ):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    guard.schedules(source)

    def test_bom_and_control_character_workflows_fail_closed(self):
        for doc in ('\ufeffon:\n  schedule:\n    - cron: "0 0 * * *"\n',
                    'on:\n  schedule:\n    - cron: "0 0 * * *"\n\x1b[0m'):
            with self.subTest(doc=repr(doc)):
                with self.assertRaisesRegex(ValueError, 'unsafe control'):
                    guard.schedules(doc)

    def test_conservative_month_and_day_of_month(self):
        self.assertEqual(len(guard.weekly_slots('0 3 1 2 *')), 7)
        self.assertEqual(len(guard.weekly_slots('0 3 * * 1')), 1)

    def test_explicit_yaml_mapping_keys_are_fail_closed(self):
        # Both explicit-key forms can be valid YAML, but they must never be
        # misclassified as a workflow without scheduled triggers.
        payloads = (
            ('? on\n: {schedule: [{cron: "0 9 * * 1"}]}\n', "top-level"),
            ('!!str on:\n  schedule:\n    - cron: "0 9 * * 1"\n', "top-level"),
            ('on:\n  ? schedule\n  :\n    - cron: "0 9 * * 1"\n', "trigger"),
            ('on:\n  !!str schedule:\n    - cron: "0 9 * * 1"\n', "trigger"),
        )
        for source, reason in payloads:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, reason):
                    guard.schedules(source)

    def test_explicit_keys_outside_on_cannot_hide_triggers(self):
        # Invalid or noncanonical root structures cannot produce a false
        # proof of zero planned jobs.
        with self.assertRaisesRegex(ValueError, "top-level"):
            guard.schedules('? on\n: schedule\n')

    def test_timezone_aware_schedules_explicitly_fail_closed(self):
        # GitHub accepts IANA schedule zones, but this risk model counts
        # UTC weekminutes. Treating local 09:00 as 09:00 UTC would undercount.
        for key in ("timezone", "'timezone'", '"timezone"'):
            for zone in ("Europe/London", "America/New_York"):
                source = (
                    'on:\n  schedule:\n'
                    '    - cron: "0 9 * * 1"\n'
                    '      ' + key + ': "' + zone + '"\n'
                )
                with self.subTest(key=key, zone=zone):
                    with self.assertRaisesRegex(ValueError, "timezone-safe"):
                        guard.schedules(source)

    def test_utc_schedule_without_timezone_continues_to_work(self):
        self.assertEqual(
            guard.schedules('on:\n  schedule:\n    - cron: "0 9 * * 1"\n'),
            ['0 9 * * 1'],
        )

    def test_expanded_full_month_day_range_must_not_narrow_to_weekday(self):
        # These expressions are NOT equivalent to a literal wildcard under
        # cron DOM/DOW OR matching. Every day of month can fire.
        for dom in ('1-31', '*/1', '1-30,31', '1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31'):
            with self.subTest(day_of_month=dom):
                self.assertEqual(
                    len(guard.weekly_slots('0 9 ' + dom + ' * 1')),
                    7,
                )
        self.assertEqual(len(guard.weekly_slots('0 9 * * 1')), 1)

    def test_dom_weekday_or_semantics_trigger_overbudget_weekwide(self):
        baseline = {
            'base-%d.yml' % i: ['0 9 * * 1'] for i in range(4)
        }
        # New workflow fires at 09:00 EVERY DAY because 1-31 is explicitly
        # restricted even though its expanded set covers all month days.
        candidate = {**baseline, 'new.yml': ['0 9 1-31 * 1']}
        report = guard.compare(baseline, candidate)
        self.assertEqual(report['new_over_budget_slots'], 1)
        self.assertEqual(report['sample'][0]['weekday_sunday_zero'], 1)
        self.assertEqual(report['candidate_peak'], 5)
        self.assertEqual(report['sample'][0]['changed_source_count'], 1)

    def test_restricted_dom_uses_all_weekdays_even_with_specific_month(self):
        self.assertEqual(
            len(guard.weekly_slots('14 6 1-31 2 4')),
            7,
        )
        self.assertEqual(
            len(guard.weekly_slots('14 6 * 2 4')),
            1,
        )


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

    def test_collision_receipt_identifies_only_changed_schedule_sources(self):
        baseline = {f'.github/workflows/base-{i}.yml': ['0 9 * * 1']
                    for i in range(4)}
        candidate = {**baseline,
                     '.github/workflows/new.yml': ['0 9 * * 1']}
        receipt = guard.compare(baseline, candidate)
        self.assertEqual(receipt["new_over_budget_slots"], 1)
        sample = receipt["sample"][0]
        self.assertEqual(sample["candidate_source_count"], 5)
        self.assertEqual(sample["changed_source_count"], 1)
        self.assertEqual(sample["new_or_changed_sources"], [
            {"workflow": ".github/workflows/new.yml", "cron": "0 9 * * 1"}
        ])
        self.assertEqual(len(sample["candidate_sources"]), 5)
        self.assertEqual(sample["weekday_sunday_zero"], 1)

    def test_duplicate_schedule_entry_attributed_without_false_new_owner(self):
        baseline = {f'w{i}.yml': ['0 9 * * 1'] for i in range(4)}
        candidate = {**baseline, 'w0.yml': ['0 9 * * 1', '0 9 * * 1']}
        receipt = guard.compare(baseline, candidate)
        sample = receipt["sample"][0]
        self.assertEqual(sample["changed_source_count"], 1)
        self.assertEqual(sample["new_or_changed_sources"], [
            {"workflow": "w0.yml", "cron": "0 9 * * 1"}
        ])

    def test_attribution_is_bounded_stable_and_source_only(self):
        baseline = {f'w{i:02d}.yml': ['30 9 * * 3'] for i in range(10)}
        candidate = {**baseline, **{
            f'new-{i:02d}.yml': ['30 9 * * 3'] for i in range(11)}}
        report = guard.compare(baseline, candidate)
        one = report["sample"][0]
        self.assertEqual(one["candidate_source_count"], 21)
        self.assertEqual(one["changed_source_count"], 11)
        self.assertEqual(len(one["candidate_sources"]), 8)
        self.assertEqual(len(one["new_or_changed_sources"]), 8)
        self.assertEqual(one["new_or_changed_sources"][0]["workflow"],
                         "new-00.yml")
        self.assertEqual(report["sample"], guard.compare(baseline, candidate)["sample"])
        self.assertFalse(report["mutation_performed"])
        self.assertFalse(report["deploy_authorized"])

    def test_weekday_only_schedule_does_not_raise_unrelated_weekend(self):
        baseline = {f'w{i}.yml': ['0 12 * * 1'] for i in range(4)}
        report = guard.compare(baseline, {**baseline, 'new.yml': ['0 12 * * 2']})
        self.assertEqual(report['new_over_budget_slots'], 0)



class ScheduleWorkflowAdmissionTests(unittest.TestCase):
    """Prevent the guard's own PR trigger from shrinking to private paths."""

    def test_runs_for_every_changed_workflow_source(self):
        source = Path(
            '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        self.assertIn('  pull_request:', source)
        self.assertIn('    branches: [main]', source)
        self.assertIn('      - .github/workflows/**', source)
        self.assertIn('      - scripts/zcloud_schedule_burst_budget_20261016.py',
                      source)
        self.assertIn('      - tests/test_zcloud_schedule_burst_budget_20261016.py',
                      source)

    def test_uses_only_hosted_read_only_source_check(self):
        source = Path(
            '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        self.assertIn('  contents: read', source)
        self.assertNotIn('actions: write', source)
        self.assertNotIn('contents: write', source)
        self.assertNotIn('runs-on: self-hosted', source)
        self.assertNotIn('runs-on: [self-hosted', source)
        self.assertNotIn('  workflow_dispatch:', source)
        self.assertIn('persist-credentials: false', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn('test "$HEAD_REPO" = "$GITHUB_REPOSITORY"', source)

    def test_exact_main_ancestor_check_precedes_candidate_test_execution(self):
        source = Path(
            '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        ancestry = 'git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"'
        self.assertIn(ancestry, source)
        self.assertLess(
            source.index(ancestry),
            source.index('      - name: Run adversarial Git-only regression'),
        )

    def test_untrusted_pr_fails_before_checkout_not_job_level_skip(self):
        source = Path(
            '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        first = source.index('      - name: Reject untrusted PR event')
        checkout = source.index('      - name: Fetch exact immutable candidate')
        self.assertLess(first, checkout)
        self.assertNotIn('    if: >-', source)
        self.assertIn('test "$HEAD_REPO" = "$GITHUB_REPOSITORY"', source)
        self.assertIn('test "$BASE_REPO" = "$GITHUB_REPOSITORY"', source)

    def test_actual_hosted_admission_shell_denies_fork_and_bad_ref(self):
        import os

        source = Path(
            '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        admission = source.split(
            '      - name: Reject untrusted PR event before any source checkout\n',
            1,
        )[1].split('      - name: Fetch exact immutable candidate', 1)[0]
        run = admission.split('        run: |\n', 1)[1]
        script = '\n'.join(
            line[10:] for line in run.splitlines() if line.startswith('          ')
        )
        base = {
            'GITHUB_REPOSITORY': 'Zennay/zCloud',
            'GITHUB_BASE_REF': 'main',
            'EVENT_KIND': 'pull_request',
            'HEAD_REPO': 'Zennay/zCloud',
            'BASE_REPO': 'Zennay/zCloud',
        }
        for changes, should_pass in (
            ({}, True),
            ({'HEAD_REPO': 'other/unsafe'}, False),
            ({'BASE_REPO': 'other/unsafe'}, False),
            ({'GITHUB_BASE_REF': 'feature'}, False),
            ({'EVENT_KIND': 'workflow_dispatch'}, False),
            ({'GITHUB_REPOSITORY': 'other/unsafe'}, False),
        ):
            with self.subTest(changes=changes):
                result = subprocess.run(
                    ['bash', '-c', script],
                    env={**os.environ, **base, **changes},
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode == 0, should_pass,
                                 result.stderr)

class GitSourceTests(unittest.TestCase):
    def setUp(self):
        # Git's detached housekeeping may briefly race with deletion of a
        # disposable fixture object directory on hosted runners. Prevent
        # auto-maintenance and tolerate cleanup-only races; never weaken
        # source assertions or hide failed Git commands.
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '.github' / 'workflows'
        self.path.mkdir(parents=True)
        self.shell('init', '-q')
        self.shell('config', 'gc.auto', '0')
        self.shell('config', 'maintenance.auto', 'false')
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

    def test_duplicate_git_tree_paths_fail_closed_before_budget_calculation(self):
        sha = self.write('original.yml', yaml('0 9 * * 1'))
        real_git = guard.git

        def duplicated_tree(*args):
            original = real_git(*args)
            if args and args[0] == 'ls-tree':
                return original + original
            return original

        with patch.object(guard, 'git', duplicated_tree):
            with self.assertRaisesRegex(ValueError, "duplicate workflow path"):
                guard.read_tree(sha)

    def test_real_git_quoted_schedule_source_counts_in_candidate(self):
        for number in range(4):
            self.write(f'base-{number}.yml', yaml('0 9 * * 1'))
        baseline = self.shell('rev-parse', 'HEAD')
        quoted = 'on:\n  "schedule":\n    - "cron": "0 9 * * 1"\n'
        candidate = self.write('quoted.yml', quoted)
        report = guard.compare(guard.read_tree(baseline),
                               guard.read_tree(candidate))
        self.assertEqual(report['new_over_budget_slots'], 1)
        self.assertEqual(report['sample'][0]['new_or_changed_sources'], [
            {'workflow': '.github/workflows/quoted.yml', 'cron': '0 9 * * 1'}
        ])

    def test_workflow_symlink_rejected(self):
        self.write('seed.yml', yaml('2 * * * *'))
        (self.path / 'link.yml').symlink_to('seed.yml')
        self.shell('add', '-A')
        self.shell('commit', '-qm', 'symlink fixture')
        with self.assertRaisesRegex(ValueError, 'git mode'):
            guard.read_tree(self.shell('rev-parse', 'HEAD'))

    def test_git_filename_with_newline_cannot_spoof_owning_workflow(self):
        self.write('seed.yml', yaml('0 5 * * *'))
        self.write('other\nmalicious.yml', yaml('0 5 * * *'))
        with self.assertRaisesRegex(ValueError, 'filename contains control'):
            guard.read_tree(self.shell('rev-parse', 'HEAD'))

    def test_unicode_bidi_and_invisible_git_workflow_names_fail_closed(self):
        # Git allows Unicode direction overrides, zero-width format controls
        # and paragraph separators in file paths; review receipts must not
        # be spoofed by invisible characters in a workflow name.
        for special in ("\u202e", "\u2028", "\u200b"):
            with self.subTest(special=repr(special)):
                self.write('evil' + special + 'mirror.yml', yaml('0 5 * * 1'))
                with self.assertRaisesRegex(ValueError, "formatting characters"):
                    guard.read_tree(self.shell('rev-parse', 'HEAD'))
                (self.path / ('evil' + special + 'mirror.yml')).unlink()
                self.shell('add', '-A')
                self.shell('commit', '-qm', 'remove unicode fixture')

    def test_oversize_git_blob_is_rejected_before_materialization(self):
        oversized = yaml('0 9 * * 1') + '#' + 'x' * guard.MAX_BLOB
        commit = self.write('large.yml', oversized)
        original = guard.git
        commands = []

        def trace_blob_reads(*args):
            commands.append(args)
            if args[:2] == ('cat-file', 'blob'):
                raise AssertionError('oversized blob materialized before size gate')
            return original(*args)

        with patch.object(guard, 'git', trace_blob_reads):
            with self.assertRaisesRegex(ValueError, 'source exceeds byte budget'):
                guard.read_tree(commit)
        self.assertTrue(any(a[:2] == ('cat-file', '-s') for a in commands))
        self.assertFalse(any(a[:2] == ('cat-file', 'blob') for a in commands))

    def test_max_git_blob_size_is_accepted_at_exact_boundary(self):
        source = yaml('0 9 * * 1')
        padding = '#' + 'x' * (guard.MAX_BLOB - len(source) - 1)
        commit = self.write('boundary.yml', source + padding)
        self.assertEqual(
            len((self.path / 'boundary.yml').read_bytes()),
            guard.MAX_BLOB,
        )
        self.assertEqual(
            guard.read_tree(commit)['.github/workflows/boundary.yml'],
            ['0 9 * * 1'],
        )

    def test_total_schedule_count_caps_real_git_source_expansion(self):
        # One workflow with many small valid cron entries is enough to force
        # excessive week-slot expansion unless bounded *before* comparison.
        lines = "on:\n  schedule:\n"
        entry = '    - cron: "0 9 * * 1"\n'
        under_limit = self.write(
            'oversized-schedule.yml',
            lines + entry * guard.MAX_SCHEDULE_ENTRIES,
        )
        self.assertEqual(
            len(guard.read_tree(under_limit)['.github/workflows/oversized-schedule.yml']),
            guard.MAX_SCHEDULE_ENTRIES,
        )
        over_limit = self.write(
            'oversized-schedule.yml',
            lines + entry * (guard.MAX_SCHEDULE_ENTRIES + 1),
        )
        with self.assertRaisesRegex(ValueError, "scheduled cron entry count"):
            guard.read_tree(over_limit)

    def test_case_ambiguous_workflow_yaml_suffix_fail_closed(self):
        for extension in ('.YML', '.Yaml', '.yMl'):
            filename = 'alternate' + extension
            with self.subTest(extension=extension):
                self.write(filename, yaml('0 9 * * 1'))
                with self.assertRaisesRegex(ValueError, 'ambiguous workflow filename'):
                    guard.read_tree(self.shell('rev-parse', 'HEAD'))
                (self.path / filename).unlink()
                self.shell('add', '-A')
                self.shell('commit', '-qm', 'remove case-variant yaml fixture')

    def test_unknown_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, 'immutable'):
            guard.read_tree('main')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            guard.read_tree('0' * 41)

    def run_hosted_provenance_step(self, baseline, candidate):
        import os
        source = (
            Path(guard.__file__).resolve().parent.parent
            / '.github/workflows/zcloud-schedule-burst-budget-20261016.yml'
        ).read_text(encoding='utf-8')
        provenance = source.split(
            '      - name: Verify immutable provenance before executing candidate tests\n',
            1,
        )[1].split('      - name: Run adversarial Git-only regression and compile', 1)[0]
        script_block = provenance.split('        run: |\n', 1)[1]
        shell_script = '\n'.join(
            line[10:] for line in script_block.splitlines()
            if line.startswith('          ')
        )
        return subprocess.run(
            ['bash', '-c', shell_script],
            cwd=self.root,
            env={**os.environ, 'HEAD_SHA': candidate, 'BASE_SHA': baseline},
            text=True, capture_output=True, timeout=10,
        )

    def test_exact_hosted_provenance_shell_accepts_fresh_git_history(self):
        baseline = self.write('baseline.yml', yaml('0 9 * * 1'))
        head = self.write('head.yml', yaml('1 9 * * 1'))
        result = self.run_hosted_provenance_step(baseline, head)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_hosted_provenance_shell_blocks_diverged_pr_history(self):
        self.write('seed.yml', yaml('0 9 * * 1'))
        initial_branch = self.shell('symbolic-ref', '--short', 'HEAD')
        self.shell('checkout', '-qb', 'stale-side')
        head = self.write('side.yml', yaml('0 9 * * 1'))
        self.shell('checkout', '-q', initial_branch)
        baseline = self.write('new-main.yml', yaml('0 9 * * 1'))
        self.shell('checkout', '-q', 'stale-side')
        result = self.run_hosted_provenance_step(baseline, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Denied: PR head must descend from exact main baseline',
                      result.stderr)

    def test_real_git_tree_and_blob_sha_are_not_commit_provenance(self):
        commit = self.write('provenance.yml', yaml('0 5 * * 1'))
        self.assertEqual(
            guard.read_tree(commit)['.github/workflows/provenance.yml'],
            ['0 5 * * 1'],
        )
        for sha in (
            self.shell('rev-parse', 'HEAD^{tree}'),
            self.shell('hash-object', '.github/workflows/provenance.yml'),
        ):
            with self.subTest(object_sha=sha):
                self.assertEqual(len(sha), 40)
                with self.assertRaisesRegex(ValueError, 'immutable Git commit'):
                    guard.read_tree(sha)

    def cli(self, baseline, candidate, *extra):
        # Exercise the production entrypoint, not a mocked in-process call:
        # exit 0 = admissible, 1 = budget deny, 2 = source/proof deny.
        import json
        import sys
        command = [
            sys.executable, str(Path(guard.__file__).resolve()),
            '--baseline-sha', baseline, '--candidate-sha', candidate,
            *extra,
        ]
        process = subprocess.run(
            command, cwd=self.root, text=True, capture_output=True,
            timeout=15,
        )
        self.assertFalse(process.stderr, process.stderr)
        report = json.loads(process.stdout)
        self.assertFalse(report['mutation_performed'])
        self.assertFalse(report['deploy_authorized'])
        self.assertTrue(report['observation_only'])
        return process.returncode, report

    def test_cli_exit_one_denies_new_collision_with_exact_git_shas(self):
        for i in range(4):
            self.write(f'base-{i}.yml', yaml('0 9 * * 1'))
        before = self.shell('rev-parse', 'HEAD')
        after = self.write('collision.yml', yaml('0 9 * * 1'))
        code, report = self.cli(before, after)
        self.assertEqual(code, 1)
        self.assertEqual(report['baseline_sha'], before)
        self.assertEqual(report['candidate_sha'], after)
        self.assertEqual(report['new_over_budget_slots'], 1)
        self.assertEqual(report['sample'][0]['new_or_changed_sources'], [
            {'workflow': '.github/workflows/collision.yml',
             'cron': '0 9 * * 1'},
        ])

    def test_cli_exit_zero_allows_spread_schedule(self):
        for i in range(4):
            self.write(f'base-{i}.yml', yaml('0 9 * * 1'))
        before = self.shell('rev-parse', 'HEAD')
        after = self.write('spread.yml', yaml('1 9 * * 1'))
        code, report = self.cli(before, after)
        self.assertEqual(code, 0)
        self.assertEqual(report['new_over_budget_slots'], 0)
        self.assertEqual(report['candidate_crons'], report['baseline_crons'] + 1)

    def test_cli_exit_two_denies_unclassifiable_workflow_source(self):
        before = self.write('good.yml', yaml('0 9 * * 1'))
        after = self.write(
            'bad.yml', 'on: {schedule: [{cron: "0 9 * * 1"}]}\n'
        )
        code, report = self.cli(before, after)
        self.assertEqual(code, 2)
        self.assertIn('inline or aliased on', report['error'])
        self.assertNotIn('baseline_sha', report)

    def test_cli_exit_two_denies_tree_sha_misrepresented_as_commit(self):
        commit = self.write('good.yml', yaml('0 9 * * 1'))
        tree = self.shell('rev-parse', 'HEAD^{tree}')
        code, report = self.cli(tree, commit)
        self.assertEqual(code, 2)
        self.assertIn('immutable Git commit', report['error'])
        self.assertNotIn('candidate_sha', report)

    def test_cli_denies_stale_pr_that_appears_safe_before_merge(self):
        # Real diverged Git history: main has a burst near the cap, while a
        # stale feature head omits main's newer crons. Naive source-tree
        # comparison would falsely permit adding a fifth overlapping run.
        self.write('seed.yml', yaml('0 9 * * 1'))
        primary_branch = self.shell('symbolic-ref', '--short', 'HEAD')
        self.shell('checkout', '-qb', 'stale-cron-branch')
        stale_head = self.write('new.yml', yaml('0 9 * * 1'))
        self.shell('checkout', '-q', primary_branch)
        for i in range(4):
            self.write(f'main-{i}.yml', yaml('0 9 * * 1'))
        exact_main = self.shell('rev-parse', 'HEAD')
        # Missing main schedules make an unsafe merge look harmless.
        unmerged = guard.compare(
            guard.read_tree(exact_main), guard.read_tree(stale_head),
        )
        self.assertEqual(unmerged['new_over_budget_slots'], 0)
        code, receipt = self.cli(exact_main, stale_head)
        self.assertEqual(code, 2)
        self.assertIn('exact main baseline ancestry', receipt['error'])
        self.assertFalse(receipt['deploy_authorized'])

    def test_cli_accepts_candidate_with_exact_main_ancestor(self):
        self.write('seed.yml', yaml('0 9 * * 1'))
        exact_main = self.shell('rev-parse', 'HEAD')
        head = self.write('new.yml', yaml('1 9 * * 1'))
        code, receipt = self.cli(exact_main, head)
        self.assertEqual(code, 0)
        self.assertEqual(receipt['baseline_sha'], exact_main)
        self.assertEqual(receipt['candidate_sha'], head)

    def test_cli_exit_two_denies_out_of_range_budget_override(self):
        commit = self.write('good.yml', yaml('0 9 * * 1'))
        code, report = self.cli(commit, commit, '--burst-limit', '0')
        self.assertEqual(code, 2)
        self.assertIn('burst limit outside', report['error'])

    def test_candidate_source_bad_cron_rejected(self):
        sha = self.write('bad.yml', yaml('abc * * * *'))
        with self.assertRaises(ValueError):
            guard.read_tree(sha)


if __name__ == '__main__':
    unittest.main()
