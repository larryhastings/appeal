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
    import big.text
except ImportError:
    sys.exit("Can't import big to copy from!  Giving up.")

big_text_py = pathlib.Path(big.text.__file__)
appeal_runtime_py = (pathlib.Path(__file__).resolve().parent.parent
                     / 'appeal' / 'runtime.py')
assert appeal_runtime_py.exists()

source = big_text_py.read_text(encoding='utf-8')
destination = original = appeal_runtime_py.read_text(encoding='utf-8')

# the warehouse folds together snippets borrowed from big and
# Appeal's own; whitelist just big's (the 'big ' name prefix).
updated = sync_snippets(source, destination,
                        (lambda name: name.startswith('big ')))

if updated == original:
    print(f'{appeal_runtime_py} unchanged.')
else:
    appeal_runtime_py.write_text(updated, encoding='utf-8')
    print(f'{appeal_runtime_py} updated.')
