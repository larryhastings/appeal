#!/usr/bin/env python3
#
# weather3 -- the PROCESSOR-compiled sample.  `import compiled3 as appeal`
# runs the generated parser (one generic engine in appeal.runtime over
# tiny Converter classes); flip the 1 to 0 for the real appeal.  The
# decorated commands don't change either way.
#
# Regenerate compiled3.py after editing:  python regenerate3.py
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.dirname(_here))

if 1:
    import compiled3 as appeal
else:
    import appeal


app = appeal.Appeal('weather', version='1.0')


@app.command()
def report(city, *, units='C', verbose=False):
    degrees = 21 if units == 'C' else 70
    print(f'{city}: {degrees}\N{DEGREE SIGN}{units}, partly cloudy')
    if verbose:
        print('barometric mood: cautiously optimistic')


@app.command()
def forecast(city, days: int = 3):
    for day in range(1, days + 1):
        print(f'{city} day {day}: sunny, probably')


@app.command()
def sync(source: appeal.split(':'), *,
         verbose: appeal.counter() = 0,
         tag: appeal.accumulator[str] = (),
         mode: appeal.validate('fast', 'safe') = 'safe'):
    print(f'sync {source} verbose={verbose} tags={list(tag)} mode={mode}')


if __name__ == '__main__':
    sys.exit(app.main())
