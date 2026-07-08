#!/usr/bin/env python3

# Re-syncs Appeal's borrowed copies of big's snippets (inside
# appeal/runtime.py, the warehouse) after big changes.  The copying
# lives in big--this is just a thin wrapper that knows Appeal's
# paths and snippet-name prefix.
#
# Uses whatever big `import big` finds--no path games; the source
# of truth is that big's own text.py, located via __file__.

import pathlib
import sys

try:
    from big.snip import main
    import big.text
except ImportError:
    sys.exit("Can't import big to copy from!  Giving up.")

big_text_py_path = pathlib.Path(big.text.__file__)

appeal_dir = pathlib.Path(__file__).resolve().parent.parent
appeal_runtime_py_path = appeal_dir / 'appeal' / 'runtime.py'
assert appeal_runtime_py_path.exists()

sys.exit(main([
    'sync',
    str(big_text_py_path),
    str(appeal_runtime_py_path),
    'big ',
    ]))
