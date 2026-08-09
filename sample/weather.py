#!/usr/bin/env python3
#
# weather -- the sample program, written the ONE way Appeal
# programs are written: import appeal, make an app, decorate your
# commands.  The same file runs in-process (against installed
# appeal) and shipped (against the compiled standalone module)--
# the try/except below is the whole difference, and the
# `recompile` flourish regenerates the module automatically
# whenever it's missing.  That flourish is optional; a shipped
# program only needs the try/except.

"""
Weather, freshly invented: report today's sky, or forecast the
next few days, with data made up on the spot.
"""

import sys

try:
    import standalone as appeal      # shipped: the compiled parser
    recompile = False
except ImportError:
    # dev box: the real thing.  (The path pin uses THIS repo's
    # appeal instead of any installed one--a real program would
    # just `import appeal`.)
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    import appeal
    recompile = True

app = appeal.Appeal('weather')


@app.command()
def report(city, *, units='C', verbose=False):
    """
    Report the current weather for a city.

    The temperature is invented fresh every run, which is about as
    accurate as the real forecasters manage.

    ## Arguments

    city
    : Which city to report on.

    ## Options

    units
    : Temperature scale: `C` or `F`.

    verbose
    : Also narrate the barometric mood.
    """
    degrees = 21 if units == 'C' else 70
    print(f'{city}: {degrees}\N{DEGREE SIGN}{units}, partly cloudy')
    if verbose:
        print('barometric mood: cautiously optimistic')


@app.command()
def forecast(city, days: int = 3):
    """
    Forecast the next few days.

    ## Arguments

    city
    : Which city to forecast.

    days
    : How many days ahead.
    """
    for day in range(1, days + 1):
        print(f'{city} day {day}: sunny, probably')


if recompile:
    print('Recompiling standalone.py ...', file=sys.stderr)
    app.standalone('standalone.py')
    sys.exit('... done.  Rerun me: I import the compiled parser now.')

if __name__ == '__main__':
    sys.exit(app.main())
