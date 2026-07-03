#!/usr/bin/env python3
#
# atest -- a tiny, low-ceremony test harness.
#
# You can test the way you write code, not the way unittest insists:
#
#   * bare "assert a == b", never self.assertWhicheverOne(),
#   * no mandatory base class, no mandatory methods, no mandatory main(),
#   * plain "def test_foo(): ..." functions are first-class,
#   * on a failing assert you get the SAME rich, type-aware diff
#     unittest's assertEqual gives you (we reuse unittest's own machinery),
#   * and you get that diff even with *zero* ceremony -- a file that just
#     does "import atest" and then bare module-level asserts gets it,
#     because atest installs an excepthook.
#
# It's stdlib-only.  unittest.TestCase classes still work if you want them.

import ast
import inspect
import linecache
import sys
import traceback as _traceback
import unittest
from pathlib import Path

__all__ = [
    "TestCase", "skip", "skipIf", "skipUnless", "expectedFailure",
    "preload", "main", "run", "register_type_equality", "explain",
    "ExplainResult",
]

# re-exports, so a test file needn't also "import unittest"
TestCase = unittest.TestCase
skip = unittest.skip
skipIf = unittest.skipIf
skipUnless = unittest.skipUnless
expectedFailure = unittest.expectedFailure


def preload(package):
    """
    Put the local checkout of `package` on sys.path, so tests run against
    the source tree.  Needed when you run a file *by path* from inside
    tests/ (sys.path[0] is then tests/, not the project root).  A no-op
    when the project root is already on the path (e.g. a hoisted file).
    """
    d = Path(sys.argv[0]).resolve().parent
    while True:
        if (d / package / "__init__.py").is_file():
            break
        if d == d.parent:
            raise FileNotFoundError(f"couldn't find {package}/__init__.py above {sys.argv[0]!r}")
        d = d.parent
    if str(d) not in sys.path:
        sys.path.insert(1, str(d))
    return d


##
## The explainer.  Given a traceback whose deepest frame raised an
## AssertionError from a bare `assert`, print the operands' values and,
## for an `==` assertion, unittest's rich type-aware diff.
##
## Everything is read from the failed frame or handed to unittest; we
## never re-evaluate the asserted expression, so no side effects fire.
##

# a throwaway TestCase instance, only so we can borrow assertEqual's
# beautiful difflib-based, type-aware failure messages.
class _Helper(unittest.TestCase):
    def runTest(self):  # pragma: no cover
        pass
_helper = _Helper()
_helper.maxDiff = None


def register_type_equality(type, function):
    """
    Teach the explainer how to diff your own type, exactly like unittest's
    TestCase.addTypeEqualityFunc.  `function(a, b, msg=None)` should raise
    an AssertionError (with a nice message) when a != b.  After this,
    `assert a == b` on two of your objects prints that message.

    (This is how you'd add the equivalent of big's assertEqualLinkedList.)
    """
    _helper.addTypeEqualityFunc(type, function)


def _safe_value(node, frame):
    """(ok, value) for a side-effect-free operand -- a name or a literal.
    Anything else (a call, a subscript, ...) returns (False, None) so we
    don't re-run it."""
    if isinstance(node, ast.Name):
        for scope in (frame.f_locals, frame.f_globals):
            if node.id in scope:
                return True, scope[node.id]
        return False, None
    if isinstance(node, ast.Constant):
        return True, node.value
    return False, None


def _rich_diff(test, frame):
    """If `test` is `a == b` and both sides are safely readable, return
    unittest's rich diff string; else None."""
    if not (isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return None
    ok_l, left = _safe_value(test.left, frame)
    ok_r, right = _safe_value(test.comparators[0], frame)
    if not (ok_l and ok_r):
        return None
    try:
        _helper.assertEqual(left, right)
    except AssertionError as e:
        return str(e)
    except Exception:
        return None
    return None  # they compared equal after all; nothing to say


def explain(tb, write):
    """Print an explanation of a failed bare-assert to `write` (a callable
    taking a string)."""
    try:
        while tb.tb_next:
            tb = tb.tb_next
        frame = tb.tb_frame
        src = linecache.getline(frame.f_code.co_filename, tb.tb_lineno).strip()
        node = ast.parse(src).body[0]
        if not isinstance(node, ast.Assert):
            return
        diff = _rich_diff(node.test, frame)
        if diff:
            write("    " + diff.replace("\n", "\n    ") + "\n")
            return
        # fall back to showing the values of the names in the assert
        shown = []
        seen = set()
        for n in ast.walk(node.test):
            if isinstance(n, ast.Name) and n.id not in seen:
                seen.add(n.id)
                ok, value = _safe_value(n, frame)
                if ok:
                    shown.append((n.id, value))
        if shown:
            write("    where:\n")
            for name, value in shown:
                write(f"        {name} = {value!r}\n")
    except Exception:
        pass


##
## Zero-ceremony mode: importing atest installs an excepthook, so even a
## file that just does bare module-level asserts (no TestCase, no main)
## gets the explanation printed after the traceback.
##

_prev_excepthook = sys.excepthook
def _excepthook(etype, value, tb):
    _prev_excepthook(etype, value, tb)
    if issubclass(etype, AssertionError):
        explain(tb, sys.stderr.write)
sys.excepthook = _excepthook


##
## unittest integration: a TextTestResult that explains failures, made
## the default so a plain unittest.main() (in any file that imported
## atest) picks it up too.
##

class ExplainResult(unittest.TextTestResult):
    def addFailure(self, test, err):
        super().addFailure(test, err)
        explain(err[2], self.stream.write)
unittest.runner.TextTestRunner.resultclass = ExplainResult


##
## The runner: runs plain "def test_*()" functions (no base class needed)
## *and* any unittest.TestCase subclasses in the module, in one tally.
##

def _main_module():
    return sys.modules["__main__"]


def run(module=None, verbose=True, exit=False):
    """
    Run the tests in `module` (default: the __main__ module).  Discovers:

        * plain "def test_*()" functions that take no required arguments
          (a function with required parameters is yours to call by hand,
          so discovery skips it), and
        * unittest.TestCase subclasses.

    Returns (tests_run, failures).  If exit is true, sys.exit()s 0/1.
    """
    if module is None:
        module = _main_module()

    total = 0
    failures = 0
    members = [(name, getattr(module, name)) for name in sorted(vars(module))]

    # plain test_* functions
    for name, obj in members:
        if not (name.startswith("test_") and inspect.isfunction(obj)):
            continue
        required = [p for p in inspect.signature(obj).parameters.values()
                    if p.default is p.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if required:
            continue  # parametrized: you call it yourself
        total += 1
        try:
            obj()
            if verbose:
                print(f"ok   {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            explain(sys.exc_info()[2], sys.stdout.write)
            _traceback.print_exc()

    # unittest.TestCase subclasses
    cases = [obj for name, obj in members
             if isinstance(obj, type) and issubclass(obj, unittest.TestCase)]
    if cases:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for case in cases:
            suite.addTests(loader.loadTestsFromTestCase(case))
        result = unittest.TextTestRunner(verbosity=(2 if verbose else 1)).run(suite)
        total += result.testsRun
        failures += len(result.failures) + len(result.errors)

    print(f"\n{'OK' if not failures else 'FAILED'} "
          f"({total} test{'' if total == 1 else 's'}"
          f"{'' if not failures else f', {failures} failed'})")

    if exit:
        sys.exit(1 if failures else 0)
    return total, failures


def main(exit=True):
    """The standalone-file entry point: put atest.main() at the bottom."""
    return run(module=_main_module(), exit=exit)
