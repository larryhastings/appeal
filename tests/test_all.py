#!/usr/bin/env python3
#
# tests/test_all.py
# The Appeal test driver: it runs every other suite and has no tests
# of its own (ruled 2026-07-11, restated 2026-07-16: "test_all should
# run all tests--that's the contract").  Modeled on big's test_all.py:
# import each suite and call its run_tests(run), aggregating in ONE
# process--so `coverage run tests/test_all.py` measures the whole
# thing at once.  (Appeal, unlike big, has no chicken-and-egg with its
# own test harness, so it just uses big.builtin.load.)

from big import test
from big.builtin import load
load('appeal')

import sys


modules = """
    test_appeal
    test_coverage
    test_corpus
    test_read
""".strip().split()

# test_39's SUBJECT is 3.9+ annotation spellings; on older Pythons the
# file bails at import (that's the point), so only run it where it can.
if sys.version_info >= (3, 9):
    modules.insert(1, 'test_39')
else:
    print('test_39.py not run (3.9+ spellings)')


with test.suite() as run:
    for module_name in modules:
        module = __import__(module_name)
        module.run_tests(run)
