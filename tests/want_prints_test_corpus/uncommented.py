
def foo():
    pass

want_prints = 1


def bar(a):
    if want_prints:
        print("howdy doody")
    print("bar", a)

    if 1:
        pass
        if want_prints:
            print("howdy doody")
    print("bar 2", a)

    if want_prints:
        print("howdy doody")

    print("bar 3", a)

    if want_prints:
        print("howdy doody")

        print("more stuff here")

    print("bar 4", a)

    if want_prints:
        print("howdy doody")

        print("more stuff here")
    # a comment immediately after a want prints block!
    # what will happen?  tune in tomorrow to find out!
    # same bat-time, same bat-channel!
    print("bar 5", a)

    if want_prints:
        print("howdy doody")

        print("more stuff here")

    if want_prints:
        print("howdy doody")

    print("bar 6", a)


if want_prints:
    print("printing time")


bar(a)


if want_prints:
    print("printing time")


