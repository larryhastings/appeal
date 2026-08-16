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
    """
    Report the current weather for a city.

    The temperature is invented fresh every run, which is about as
    accurate as the real forecasters manage, and considerably more
    honest about it.

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
         mode: appeal.validate('fast', 'safe') = 'safe',
         only: appeal.split(',') = (),
         define: appeal.mapping[str, int] = None):
    """
    Sync weather stations, exercising the special converters.

    ## Arguments

    source
    : A `:`-separated list of station ids (appeal.split).

    ## Options

    verbose
    : Repeatable; counts how many times it's given (appeal.counter).

    tag
    : Repeatable; collects labels (appeal.accumulator).

    mode
    : One of `fast` or `safe` (appeal.validate).

    only
    : A `,`-separated list, split into one value (appeal.split).

    define
    : `--define KEY VALUE`, repeatable, builds a dict (appeal.mapping).
    """
    print(f'sync {source} verbose={verbose} tags={list(tag)} mode={mode} '
          f'only={list(only)} define={define}')


if __name__ == '__main__':
    sys.exit(app.main())
