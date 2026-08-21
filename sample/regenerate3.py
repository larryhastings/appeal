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
from appeal.runtime import config_fingerprint, _callable_fingerprint

app = appeal.Appeal('weather', version='1.0')
source = open(os.path.join(_here, 'weather3.py')).read()
body = source.split("app = appeal.Appeal('weather', version='1.0')", 1)[1]
body = body.split("if __name__ ==", 1)[0]
namespace = {'appeal': appeal, 'app': app}
exec(body, namespace)

# the configuration identity the compiled Appeal verifies against: the version
# plus the two policies' fingerprints, baked so the sentinel default falls back
# to them (see appeal.runtime.config_fingerprint).
dm_fp = _callable_fingerprint(app.default_mappings)
do_fp = _callable_fingerprint(app.default_options)
baked = config_fingerprint(app.version, dm_fp, do_fp)

plans = [build.build_plan(namespace[name])
         for name in ('report', 'forecast', 'sync')]
path = os.path.join(_here, 'compiled3.py')
open(path, 'w').write(emit_module(plans, baked_fingerprint=baked,
                                  default_mappings_fp=dm_fp,
                                  default_options_fp=do_fp))
print(f'wrote {path}')
