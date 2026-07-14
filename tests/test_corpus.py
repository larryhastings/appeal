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
        "if getattr(appeal, '__version__', '').startswith('2.'):",
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


if __name__ == '__main__':
    sys.exit(main())
