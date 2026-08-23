#!/usr/bin/env python3
#
# tests/test_corpus.py
# Part of Appeal v2.
#
# Runs the v1 test corpus (tests/test_v1.py, 270 tests) against v2
# with a RATCHET: the names below are the known-not-yet-passing set.
# A passing test regressing fails this runner; an expected-failure
# starting to pass ALSO fails it (delete it from the list--progress
# must be recorded).  Proposal (§9): keep the tests, not the code.
#
# The list's categories, roughly:
#   * option converters with their own options ("options that take
#     options"): awaits the streaming driver
#   * v1 internals (SignatureUtilityTests, class-based commands,
#     processor preparers)
#   * v1 help/usage text formats (v2 formats differently, on purpose)
#   * read_csv / unnested / read_iterable strictness differences
#   * assorted small semantic gaps, one investigation each

import io
import sys
import unittest

from big import test

repo_dir = str(test.preload('appeal'))
sys.path.insert(0, repo_dir + '/tests')

EXPECTED_FAILURES = frozenset((
    # v2 deliberately diverges: mapping (dict[K, V]'s mechanism) now
    # collects one KEY=VALUE token per occurrence, not v1's space-
    # separated KEY VALUE (Larry's ruling, 2026-08-18).  KEY=VALUE
    # mapping--incl. the duplicate-key message and typed halves--is
    # covered by the v2 tests (test_39, test_appeal).
    'SmokeTests.test_pool_1',
    'SmokeTests.test_snooker_1',
    'BugfixRegressionTests.test_mapping_duplicate_key_message',
    'ConverterVocabularyTests.test_mapping',
    'ConverterVocabularyTests.test_mapping_typed',
    # v2 deliberately diverges: command NAMES are dash-mangled
    # (five_level_stack -> five-level-stack, the git-cherry-pick
    # convention); the v1 corpus invokes them with underscores, so
    # every one is an 'unknown command (did you mean ...-...)'.
    'SmokeTests.test_five_level_stack_1',
    'SmokeTests.test_five_level_stack_10',
    'SmokeTests.test_five_level_stack_11',
    'SmokeTests.test_five_level_stack_12',
    'SmokeTests.test_five_level_stack_13',
    'SmokeTests.test_five_level_stack_14',
    'SmokeTests.test_five_level_stack_15',
    'SmokeTests.test_five_level_stack_16',
    'SmokeTests.test_five_level_stack_2',
    'SmokeTests.test_five_level_stack_3',
    'SmokeTests.test_five_level_stack_4',
    'SmokeTests.test_five_level_stack_5',
    'SmokeTests.test_five_level_stack_6',
    'SmokeTests.test_five_level_stack_7',
    'SmokeTests.test_five_level_stack_8',
    'SmokeTests.test_five_level_stack_9',
    'SmokeTests.test_hey_argparse_watch_this_1',
    'SmokeTests.test_hey_argparse_watch_this_2',
    'SmokeTests.test_hey_argparse_watch_this_3',
    'SmokeTests.test_hey_argparse_watch_this_4',
    'SmokeTests.test_hey_argparse_watch_this_5',
    'SmokeTests.test_hey_argparse_watch_this_6',
    'SmokeTests.test_hey_argparse_watch_this_7',
    'SmokeTests.test_inferred_list_1',
    'SmokeTests.test_inferred_list_2',
    'SmokeTests.test_inferred_list_3',
    'SmokeTests.test_invalid_annotation_1_1',
    'SmokeTests.test_invalid_annotation_2_1',
    'SmokeTests.test_invalid_logging_1',
    'SmokeTests.test_mixed_groups_1',
    'SmokeTests.test_mixed_groups_10',
    'SmokeTests.test_mixed_groups_11',
    'SmokeTests.test_mixed_groups_12',
    'SmokeTests.test_mixed_groups_2',
    'SmokeTests.test_mixed_groups_4',
    'SmokeTests.test_mixed_groups_5',
    'SmokeTests.test_mixed_groups_6',
    'SmokeTests.test_mixed_groups_7',
    'SmokeTests.test_mixed_groups_8',
    'SmokeTests.test_mixed_groups_9',
    'SmokeTests.test_options_stack_1',
    'SmokeTests.test_options_stack_2',
    'SmokeTests.test_options_stack_3',
    'SmokeTests.test_options_stack_4',
    'SmokeTests.test_options_stack_5',
    'SmokeTests.test_options_stack_6',
    'SmokeTests.test_options_stack_7',
    'SmokeTests.test_options_stack_8',
    'SmokeTests.test_options_stack_9',
    'SmokeTests.test_set_path_1',
    'SmokeTests.test_set_path_2',
    'SmokeTests.test_simple_defaults_1',
    'SmokeTests.test_simple_defaults_2',
    'SmokeTests.test_simple_defaults_3',
    'SmokeTests.test_two_or_more_files_1',
    'SmokeTests.test_two_or_more_files_2',
    'SmokeTests.test_two_or_more_files_usage',
    # more dash-mangled command names (str_i_f -> str-i-f); these were
    # migrated to assert v2 messages but never reach them, blocked on the
    # command word.
    'SmokeTests.test_str_i_f_1',
    'SmokeTests.test_str_i_f_2',
    'SmokeTests.test_str_i_f_4',
    # v2 deliberately diverges: a parent command does NOT require a
    # subcommand -- there's no synthesized dispatcher, a parent runs its own
    # body ([[no-pure-dispatcher]]).  v1 rejected a bare parent.
    'SubcommandTests.test_parent_command_without_subcommand_rejected',
))


def main():
    import builtins
    import os.path
    real_stdout = sys.stdout
    real_print = builtins.print   # the corpus swaps builtins.print; it
                                  # can leak on failing paths
    corpus_path = os.path.join(repo_dir, 'tests', 'test_v1.py')
    source = open(corpus_path, 'rt', encoding='utf-8').read()
    source = source.replace(
        "if not getattr(appeal, '__version__', '').startswith('0.'):",
        "if False:")
    namespace = {'__name__': 'test_all_corpus', '__file__': corpus_path}
    exec(compile(source, corpus_path, 'exec'), namespace)

    loader = unittest.TestLoader()
    total = passed = skipped = 0
    regressions = []
    surprises = []
    for name in sorted(namespace):
        obj = namespace[name]
        if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)
                and obj is not unittest.TestCase):
            continue
        suite = loader.loadTestsFromTestCase(obj)
        count = suite.countTestCases()
        if not count:
            continue
        ran = {t.id().split('.', 1)[-1] for t in suite}
        result = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0).run(suite)
        sys.stdout = real_stdout    # the capture machinery can leak
        builtins.print = real_print  # on failing paths; undo both
        total += count
        failed = {case.id().split('.', 1)[-1]
                  for case, _ in result.failures + result.errors}
        # a skip is not a pass: it's a deferred feature's tombstone,
        # and it gets counted out loud (the make -j lesson: skips
        # hidden inside "270/270 passing" are a queue nobody reads)
        skipped += len(result.skipped)
        passed += count - len(failed) - len(result.skipped)
        regressions.extend(sorted(failed - EXPECTED_FAILURES))
        surprises.extend(sorted((ran & EXPECTED_FAILURES) - failed))

    print(f'corpus: {passed}/{total} passing, {skipped} skipped '
          f'(deferred features--see @unittest.skip labels in '
          f'test_v1.py), {len(EXPECTED_FAILURES)} expected failures')
    ok = True
    if regressions:
        ok = False
        print(f'REGRESSIONS ({len(regressions)}):')
        for name in regressions:
            print(f'    {name}')
    if surprises:
        ok = False
        print(f'NEWLY PASSING ({len(surprises)}) -- delete from '
              f'EXPECTED_FAILURES to record the progress:')
        for name in surprises:
            print(f'    {name}')
    return 0 if ok else 1


def run_tests(run=None):
    # A custom runner: the corpus tests live in test_v1.py, exec'd
    # and driven by hand (loader + EXPECTED_FAILURES ratchet), so
    # they aren't discoverable by test.run(module=...).  Feed the
    # shared aggregate so the driver's finish() sees a regression.
    if main():
        test.stats['failures'] += 1


if __name__ == '__main__':
    run_tests()
    test.finish()
