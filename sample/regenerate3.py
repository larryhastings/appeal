#!/usr/bin/env python3
#
# Regenerate compiled3.py from weather3.py's live command signatures,
# using the processor emitter (appeal/compile.py).
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))   # repo root: import appeal

import appeal
import appeal.build as build
from appeal.compile import emit_module

app = appeal.Appeal('weather', version='1.0')
source = open(os.path.join(_here, 'weather3.py')).read()
body = source.split("app = appeal.Appeal('weather', version='1.0')", 1)[1]
body = body.split("if __name__ ==", 1)[0]
namespace = {'appeal': appeal, 'app': app}
exec(body, namespace)

plans = [build.build_plan(namespace[name])
         for name in ('report', 'forecast', 'sync')]
path = os.path.join(_here, 'compiled3.py')
open(path, 'w').write(emit_module(plans))
print(f'wrote {path}')
