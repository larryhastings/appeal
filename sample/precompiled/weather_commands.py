"""The user's business logic -- knows nothing about Appeal."""


def report(city, *, units='C', verbose=False):
    degrees = 21 if units == 'C' else 70
    print(f'{city}: {degrees}\N{DEGREE SIGN}{units}, partly cloudy')
    if verbose:
        print('barometric mood: cautiously optimistic')


def forecast(city, days=3):
    for day in range(1, days + 1):
        print(f'{city} day {day}: sunny, probably')
