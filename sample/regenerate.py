#!/usr/bin/env python3
#
# Regenerate standalone/weather from the in-process app.
#
# This is the emission step--what an author runs once before
# shipping (or wires into their build).  It imports the ordinary
# in-process app and asks it for the standalone script; the
# emitted file is the whole deliverable.

import os
import shutil
import stat
import sys

sample = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(sample)
sys.path.insert(0, repo)                             # THIS appeal
sys.path.insert(0, os.path.join(sample, 'in_process'))

from weather import app

script = app.standalone(argv0='weather')

standalone = os.path.join(sample, 'standalone')
os.makedirs(standalone, exist_ok=True)

target = os.path.join(standalone, 'weather')
with open(target, 'wt', encoding='utf-8') as f:
    f.write(script)
os.chmod(target, os.stat(target).st_mode
         | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

# the business logic ships beside it, unchanged
shutil.copy(os.path.join(sample, 'in_process', 'weather_commands.py'),
            os.path.join(standalone, 'weather_commands.py'))

lines = script.count('\n')
print(f'wrote {target}: {lines} lines, {len(script)} bytes')
