#!/usr/bin/env python3
#
# weather -- ONE program, two engines.  Flip the 1 to a 0 and it
# still works: `import compiled` is the pre-compiled parser (fast,
# imports only appeal.runtime, the stdlib-only core); `import
# appeal` is the real thing (builds the parser from these
# signatures at runtime).  The decorated commands below don't
# change either way -- that's the whole point.
#
# Regenerate compiled.py after editing:  python regenerate.py

import os
import sys

# make this repo importable no matter where you run from: this
# file's directory (for `import compiled`) and the repo root (for
# `import appeal`).  A real, installed program needs neither line.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))

if 1:
    import compiled as appeal
else:
    import appeal


app = appeal.Appeal('weather', version='1.0')


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
    Forecast the next few days of imaginary but plausible weather.

    ## Arguments

    city
    : Which city to forecast.

    days
    : How many days ahead to guess.
    """
    for day in range(1, days + 1):
        print(f'{city} day {day}: sunny, probably')


@app.command()
def sync(source: appeal.split(':'), *,
         verbose: appeal.counter() = 0,
         tag: appeal.accumulator[str] = (),
         mode: appeal.validate('fast', 'safe') = 'safe'):
    """
    Sync weather stations, exercising the special converters.

    ## Arguments

    source
    : A `:`-separated list of station ids (appeal.split).

    ## Options

    verbose
    : Repeatable; counts how many times given (appeal.counter).

    tag
    : Repeatable; collects labels (appeal.accumulator).

    mode
    : One of `fast` or `safe` (appeal.validate).
    """
    print(f'sync {source} verbose={verbose} tags={list(tag)} mode={mode}')


if __name__ == '__main__':
    sys.exit(app.main())
