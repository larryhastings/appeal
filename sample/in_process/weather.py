#!/usr/bin/env python3
#
# weather -- the IN-PROCESS spelling.
#
# This is how you develop: import appeal, register the commands,
# call main().  Appeal builds and compiles the parser lazily at
# first use, every run, in memory--nothing is written to disk.
# Requires appeal (and therefore big) installed.
#
#     python3 weather.py report portland -v
#     python3 weather.py --help

import os
import sys

# use THIS repo's appeal, not any installed one (there's a stale
# 0.6.4 in site-packages); a real program would just `import appeal`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import appeal
from weather_commands import forecast, report

app = appeal.Appeal('weather')
app.command()(report)
app.command()(forecast)

if __name__ == '__main__':
    sys.exit(app.main())
