#!/usr/bin/env python3
#
# weather -- ONE program, two engines.  Flip the 1 to a 0 and it
# still works: `import compiled` is the pre-compiled parser (fast,
# needs appeal_runtime beside it); `import appeal` is the real
# thing (builds the parser from these signatures at runtime).  The
# decorated commands below don't change either way -- that's the
# whole point.

import sys

if 1:
    import compiled as appeal
else:
    # the real Appeal (pin this repo's copy for the demo; a real
    # program would just `import appeal`)
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    import appeal


app = appeal.Appeal('weather')


@app.command()
def report(city, *, units='C', verbose=False):
    "Report the current weather for a city."
    degrees = 21 if units == 'C' else 70
    print(f'{city}: {degrees}\N{DEGREE SIGN}{units}, partly cloudy')
    if verbose:
        print('barometric mood: cautiously optimistic')


@app.command()
def forecast(city, days: int = 3):
    "Forecast the next few days."
    for day in range(1, days + 1):
        print(f'{city} day {day}: sunny, probably')


if __name__ == '__main__':
    sys.exit(app.main())
