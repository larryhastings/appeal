#!/usr/bin/env python3
#
# Regenerate compiled.py from weather.py's live command signatures.
#
# Runs weather.py's registrations against the REAL appeal (never
# the compiled module) and calls app.precompile() to emit the
# parser.  Run this after editing any command:  python regenerate.py

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))   # the repo root: import appeal

import appeal

# build the same app weather.py builds, but bound to real appeal
app = appeal.Appeal('weather', version='1.0')
source = open(os.path.join(_here, 'weather.py')).read()
# take everything after the app is created, up to the __main__ guard--
# the decorator block, verbatim
body = source.split(
    "app = appeal.Appeal('weather', version='1.0')", 1)[1]
body = body.split("if __name__ ==", 1)[0]
exec(body, {'appeal': appeal, 'app': app})

path = os.path.join(_here, 'compiled.py')
app.precompile(path=path)
print(f'wrote {path}')
