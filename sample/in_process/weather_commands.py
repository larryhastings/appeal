"""
Weather, freshly invented: report today's sky, or forecast the
next few days, with data made up on the spot.
"""
# This module is the program's business logic, and it knows
# NOTHING about appeal--ordinary Python, ordinary docstrings.
# Both spellings of the program (in-process and standalone) share
# it verbatim.  Its module docstring above doubles as the
# program's own documentation (the doc chain: doc= beats the
# global command's docstring beats this).


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
