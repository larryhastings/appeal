#!/usr/bin/env python3

# Re-syncs Appeal's borrowed copies of big's snippets (inside
# appeal/runtime.py, the warehouse) after big changes.  The syncing
# logic lives in big--this is just a thin wrapper that knows
# Appeal's paths and snippet-name prefix, and calls big.snip's
# sync_snippets() library function directly.
#
# Uses whatever big `import big` finds--no path games; the source
# of truth is big's own text.py, located via __file__.

import pathlib
import sys

try:
    from big.snip import sync_snippets
    import big.markdown
    import big.stylesheet
    import big.text
except ImportError:
    sys.exit("Can't import big to copy from!  Giving up.")

appeal_runtime_py = (pathlib.Path(__file__).resolve().parent.parent
                     / 'appeal' / 'runtime.py')
assert appeal_runtime_py.exists()

destination = original = appeal_runtime_py.read_text(encoding='utf-8')

# the warehouse folds together snippets borrowed from big and
# Appeal's own; whitelist just big's (the 'big ' name prefix).
# Three source modules: the word-wrap trio (text), the
# StyleSheet render core + ANSI stylesheets (stylesheet), and
# markdown_defaults (markdown).  Each syncs only the regions it
# defines.
import re
updated = destination
for module in (big.text, big.stylesheet, big.markdown):
    source = pathlib.Path(module.__file__).read_text(encoding='utf-8')
    defined = set(re.findall(r'--8<-- start (big [^-]+?) --8<--',
                             source))
    if not defined:
        continue
    updated = sync_snippets(source, updated,
                            (lambda name, d=defined: name in d))

if updated == original:
    print(f'{appeal_runtime_py} unchanged.')
else:
    appeal_runtime_py.write_text(updated, encoding='utf-8')
    print(f'{appeal_runtime_py} updated.')
