#!/usr/bin/env python3
#
# appeal/windowed_mechanism_demo.py
#
# Shows, concretely, WHERE the scoped mechanism (PreOption -> summon the
# next converter) fails on a *args-of-converter-group (windowed), and how
# the held-list amendment fixes it.  Both are minimal stand-ins for just
# the *args binding of:
#
#   size(width: float, *, bold=False)      # width is REQUIRED
#   draw_w(shape, *sizes: size)            # sizes share --bold
#
# Run:  python -m appeal.windowed_mechanism_demo
import sys
sys.path.insert(0, '/home/larry/tron/src/appeal')
import appeal


def size(width: float, *, bold=False):
    return ('size', width, bold)

def draw_w(shape, *sizes: size):
    return ('draw_w', shape, sizes)


# --- mechanism A: scoped-style.  --bold SUMMONS the next instance -----
# (exactly what PreOption('--bold' -> summon next size) would do: fire a
# trailing --bold and it conjures a fresh instance, which then needs a
# width it never gets.)
def via_summon(rest):
    instances = []
    pending = None                      # a summoned-but-unfilled instance
    for tok in rest:
        if tok == '--bold':
            if pending is None:
                pending = {'width': None, 'bold': True}   # summon the next
            else:
                pending['bold'] = True
        else:
            if pending is not None:
                pending['width'] = float(tok)
                instances.append(pending); pending = None
            else:
                instances.append({'width': float(tok), 'bold': False})
    if pending is not None:
        # THE FAILURE: a summoned instance never received its required width
        raise ValueError(
            f"summoned size #{len(instances) + 1} has no width")
    return [('size', d['width'], d['bold']) for d in instances]


# --- mechanism B: the held-list amendment ----------------------------
# --bold between instances is HELD; the next instance to open claims it;
# any still-held at the end bind to the LAST instance (error if none).
def via_held(rest):
    instances = []
    held = False
    for tok in rest:
        if tok == '--bold':
            held = True                 # don't bind yet
        else:
            instances.append({'width': float(tok), 'bold': held})
            held = False                # the opening instance claimed it
    if held:
        if not instances:
            raise ValueError("--bold needs at least one size")
        instances[-1]['bold'] = True    # trailing -> the LAST instance
    return [('size', d['width'], d['bold']) for d in instances]


def oracle(argv):
    app = appeal.Appeal(); app.command('d')(draw_w)
    try:
        return list(app.process(['d'] + argv)[2])   # the sizes tuple
    except appeal.AppealError as e:
        return ('err', str(e)[:30])


def run(argv):
    rest = argv[1:]                     # drop the shape operand
    def attempt(fn):
        try: return fn(rest)
        except Exception as e: return ('err', str(e)[:30])
    ref = oracle(argv)
    a = attempt(via_summon)
    b = attempt(via_held)
    aok = (a == ref) or (a[0:1] == ('err',) and ref[0:1] == ('err',))
    bok = (b == ref) or (b[0:1] == ('err',) and ref[0:1] == ('err',))
    print(f"  {argv}")
    print(f"      appeal : {ref}")
    print(f"      summon : {'OK ' if aok else 'XX '}{a}")
    print(f"      held   : {'OK ' if bok else 'XX '}{b}")


if __name__ == '__main__':
    print("=== where PreOption->summon FAILS on windowed, and held fixes it ===")
    for argv in (['a', '1', '--bold', '2', '3'],     # mid-stream: both fine
                 ['a', '1', '2', '3', '--bold'],     # TRAILING: summon breaks
                 ['a', '--bold', '1', '2', '3'],
                 ['a', '--bold']):
        run(argv)
