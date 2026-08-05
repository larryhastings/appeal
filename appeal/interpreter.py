#!/usr/bin/env python3
#
# appeal/interpreter.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# Rung 1: interpret the plan tree directly.  Simple, obviously
# correct, pdb-friendly.  This is the reference implementation;
# rung 3 (codegen.py) is fuzzed against it forever.  Any divergence
# between the two is a bug in one of them, by definition.

import inspect as _inspect

from .build import all_options, help_option_strings, subtree_option_keys
from .help import merge_docs
from .plan import Terminal, NO_DEFAULT
from .runtime import (
    UsageError, absorb_take, accumulate, call_converter,
    collect_mapping, convert, fold, greedy_sizes, parse_tokens,
    check_count,
    default_template, did_you_mean, help_margin, render_command_listing,
    render_help_page,
    scoped_forces, scoped_next, scoped_resolve, scoped_rewind,
    scoped_window, scopes_for, sibling_scopes, window_options,
    )


def _feasible(remaining, suffix):
    counts, minimum = suffix
    if counts is None:
        return remaining >= minimum
    return remaining in counts


def _shared_winner(plan, given, name):
    """
    Of the option strings sharing this parameter, the key that
    spoke LAST on the command line (given is kept in
    last-occurrence order).  A parameter with one rule wins
    trivially.
    """
    keys = [o.key for o in plan.options if o.name == name]
    if len(keys) == 1:
        return keys[0]
    winner = None
    for key in given:
        if key in keys:
            winner = key
    return winner


def _option_kwargs(plan, given, usage, overlay=None, scopes=None,
                   sib=None):
    """
    Resolve a rule's own options from the parse's given table.
    overlay carries this window's claimed values for scoped keys
    (whose raw `given` entries are positional records, not values).
    sib is (scopes, summon) for sibling option groups: each
    parent's fill pops its window's claimed child options, and
    `summon` names the parent to conjure with defaults when child
    options appeared with no announcement (Larry's ruling,
    2026-07-18).
    """
    sib_scopes, sib_summon = sib if sib is not None else (None, None)
    sibling_keys = plan.sibling_keys
    kwargs = {}
    defaults = {}
    given_names = set()
    for o in plan.options:
        key = o.key
        if overlay is not None and key in overlay:
            value = overlay[key]
            given_names.add(o.name)
            if o.kind == 'flag':
                kwargs[o.name] = value
            elif o.kind == 'nullary':
                kwargs[o.name] = o.converters[0]()
            elif o.kind == 'value':
                if len(o.converters) == 1:
                    kwargs[o.name] = convert(o.converters[0], value,
                                             o.name, usage)
                else:
                    kwargs[o.name] = call_converter(
                        o.converters[0], o.converters[1:], value,
                        o.name, usage)
            elif o.kind == 'accumulate':
                kwargs[o.name] = accumulate(o.converters[0], value,
                                            o.name, usage)
            elif o.kind == 'group':
                child = o.child
                child_args, _, _ = _fill(child, list(value), 0,
                                         len(value), given, usage,
                                         scopes=scopes)
                kwargs[o.name] = child.callable(
                    *child_args, **_option_kwargs(child, given, usage,
                                                  scopes=scopes))
            elif o.kind == 'fold':
                kwargs[o.name] = fold(o.converters[0], o.converters[1:],
                                      value, o.default, o.name, usage)
            elif o.kind == 'fold1':
                kwargs[o.name] = fold(o.converters[0], o.converters[1:],
                                      (value,), o.default, o.name, usage)
            else:   # mapping
                kwargs[o.name] = collect_mapping(
                    o.converters[0], o.converters[1], value,
                    o.name, usage)
            continue
        if key not in given or (scopes is not None and key in scopes):
            # (a scoped key's raw entry is positional records, not
            # a value; a window that claimed nothing sees ABSENT--
            # the default fills)
            if sib_summon is not None and key == sib_summon:
                # conjure the first declared sibling: zero
                # operands, defaults throughout, the unannounced
                # child options bound to it
                child = o.child
                child_overlay = _claim_own(child, sib_scopes)
                child_args, _, _ = _fill(child, [], 0, 0, given, usage)
                kwargs[o.name] = child.callable(
                    *child_args,
                    **_option_kwargs(child, given, usage,
                                     overlay=child_overlay,
                                     scopes=sib_scopes))
                continue
            if o.child is not None:
                # an option group that wasn't given: its inner
                # options have nothing to attach to (sibling keys
                # excepted: another window may claim them)
                for inner in subtree_option_keys(o.child):
                    if inner in given and inner not in sibling_keys:
                        raise UsageError(
                            f"option {inner} requires {key}", usage)
            # several rules may share a parameter (per-declaration
            # @app.option); an absent rule must not clobber a given
            # sibling--defaults fill in a second pass
            if not o.kwargs_delivered:
                defaults.setdefault(o.name, o.default)
            continue
        if _shared_winner(plan, given, o.name) != key:
            # several rules share this parameter: the one whose
            # string spoke LAST on the command line wins (ruled
            # 2026-07-09; `given` is kept in last-occurrence
            # order for exactly this)
            continue
        given_names.add(o.name)
        if o.kind == 'flag':
            kwargs[o.name] = given[key]
        elif o.kind == 'nullary':
            kwargs[o.name] = o.converters[0]()
        elif o.kind == 'value':
            if len(o.converters) == 1:
                kwargs[o.name] = convert(o.converters[0], given[key],
                                         o.name, usage)
            else:
                kwargs[o.name] = call_converter(
                    o.converters[0], o.converters[1:], given[key],
                    o.name, usage)
        elif o.kind == 'accumulate':
            kwargs[o.name] = accumulate(o.converters[0], given[key],
                                        o.name, usage)
        elif o.kind == 'group':
            operands = list(given[key])
            child = o.child
            # greedy consumption grabs up to the maximum; the count
            # grabbed must still be one the group can accept
            check_count(len(operands), child.minimum, child.maximum,
                        child.valid_counts, usage, what=f"option {key}",
                        param=key)
            child_overlay = child_scopes = None
            if (sib_scopes is not None
                    and any(key == pk
                            for pk, _ in plan.sibling_parents)):
                # an announced sibling: pop this window's claimed
                # child options
                child_overlay = _claim_own(child, sib_scopes)
                child_scopes = sib_scopes
            child_args, _, _ = _fill(child, operands, 0, len(operands),
                                     given, usage)
            kwargs[o.name] = child.callable(
                *child_args, **_option_kwargs(child, given, usage,
                                              overlay=child_overlay,
                                              scopes=child_scopes))
        elif o.kind == 'fold':
            kwargs[o.name] = fold(o.converters[0], o.converters[1:],
                                  given[key], o.default, o.name, usage)
        elif o.kind == 'fold1':
            kwargs[o.name] = fold(o.converters[0], o.converters[1:],
                                  (given[key],), o.default, o.name, usage)
        else:   # mapping
            kwargs[o.name] = collect_mapping(o.converters[0], o.converters[1],
                                     given[key], o.name, usage)
    for name, default in defaults.items():
        kwargs.setdefault(name, default)
    return kwargs


def _scopes_for(plan, given):
    specs = {}
    for owner, o in all_options(plan):
        if (o.key in plan.scoped_keys
                and o.key not in plan.sibling_keys):
            # sibling-group keys bind by announcement, not by the
            # positional walk (see _sibling_scopes)
            specs[o.key] = (
                'multi' if o.kind in ('accumulate', 'mapping', 'fold')
                else 'strict' if o.kind == 'fold1'
                else 'last')
    return scopes_for(specs, given)


def _sibling_scopes(plan, given, positions, usage):
    "The announcement-ordered scopes for sibling option groups."
    if not plan.sibling_parents:
        return None, None
    specs = {}
    for owner, o in all_options(plan):
        if o.key in plan.sibling_keys:
            specs[o.key] = (
                'multi' if o.kind in ('accumulate', 'mapping', 'fold')
                else 'strict' if o.kind == 'fold1'
                else 'last')
    first_key = plan.sibling_parents[0][0]
    (first,) = [o for o in plan.options if o.key == first_key]
    return sibling_scopes(plan.sibling_parents, specs, given,
                          positions, usage,
                          summonable=first.child.minimum == 0)


def _claim_own(plan, scopes):
    """
    The live walk's pop, as it exits a window: this plan's own
    scoped options' assigned values, shaped per kind into the
    overlay _option_kwargs reads.
    """
    overlay = {}
    for o in plan.options:
        if not scopes or o.key not in scopes:
            continue
        values = scoped_next(scopes, o.key)
        if not values:
            continue
        if o.kind in ('accumulate', 'mapping', 'fold'):
            overlay[o.key] = values
        else:   # flag, value, group, fold1, nullary
            overlay[o.key] = values[0]
    return overlay


def _fill(plan, operands, i, remaining, given, usage,
          gate=0, positions=None, dry=False, reserved=None, scopes=None,
          force_key=None):
    """
    The counting automaton, interpreted: fill this plan's
    non-trailing slots from operands[i:], consuming exactly
    `remaining` operands.  Returns (positional_args, i, remaining).

    gate/positions implement the gate rule: this plan's options
    may not have appeared before `gate` operands were consumed
    (i.e., before every required group to its left was fed).

    dry runs the same walk with no user code: every structural
    decision and structural error, no conversions, no calls--
    stage 1's half of parse-before-execute.
    """
    if scopes:
        scoped_window(scopes, [o.key for o in plan.options], 'in', i,
                      force_key)
    if gate and positions and not plan.certain:
        # the gate rule applies only to skippable groups: a certain
        # group's options float free (v1, probed)--and a scoped
        # option's placement is the interval model's business
        for o in plan.options:
            if scopes and o.key in scopes:
                continue
            if o.key in given and positions.get(o.key, 0) < gate:
                raise UsageError(
                    f"option {o.key} appears too early on the command line",
                    usage)
    args = []
    for slot in plan.slots:
        if slot.trailing:
            break

        if slot.repeat:
            take = remaining - slot.suffix_after[1]
            if isinstance(slot.child, Terminal):
                if not dry:
                    for j in range(take):
                        args.append(convert(slot.child.converter,
                                            operands[i + j],
                                            slot.name, usage))
                i += take
                remaining -= take
                continue
            # a converter group: `take` operands split into
            # greedily-sized instances (v1's fill, ruled
            # 2026-08-05); options bind to instances by window
            child = slot.child
            sizes = greedy_sizes(take, child.minimum, child.maximum,
                                 slot.name, usage)
            occurrences = {o.key: given[o.key]
                           for o in child.options if o.key in given}
            givens = window_options(occurrences, i, sizes,
                                    slot.name, usage, gate=gate)
            for j, size in enumerate(sizes):
                child_args, i, _ = _fill(child, operands, i, size, {},
                                         usage, dry=dry)
                if not dry:
                    kwargs = _option_kwargs(child, givens[j], usage)
                    args.append(child.callable(*child_args, **kwargs))
            remaining -= take
            continue

        if slot.count_options is None:
            # an absorbing slot: its converter contains *args
            counts, minimum = slot.suffix_after
            chosen = absorb_take(remaining, counts, minimum,
                                 slot.child.body_minimum,
                                 not slot.required)
            if chosen is None:   # pragma: no cover -- check_count
                raise UsageError(f"can't fill {slot.name!r}", usage)
        else:
            for c in slot.count_options:
                if c <= remaining and _feasible(remaining - c,
                                                slot.suffix_after):
                    chosen = c
                    break
            else:   # pragma: no cover -- check_count unreachable
                raise UsageError(f"can't fill {slot.name!r}", usage)

        if chosen == 0 and not slot.required:
            # v1 semantics: giving one of a group's options FORCES
            # the group--it is entered with zero operands, defaults
            # and all.  Only a group that *needs* operands it can't
            # have makes the option an error.
            forced = None
            for key in subtree_option_keys(slot.child) if not isinstance(slot.child, Terminal) else ():
                if scopes and key in scopes:
                    # scoped: the interval model decides (or, in
                    # the live phase, replays its decision)
                    if not scoped_forces(scopes, (key,)):
                        continue
                elif key not in given:
                    continue
                if slot.child.body_minimum == 0:
                    forced = key
                    break
                raise UsageError(
                    f"option {key} requires {slot.name!r}", usage)
            if forced is None:
                if not dry:
                    args.append(slot.default)
                continue
            # forced: fall through and enter the group, chosen == 0
            force_key = forced if (scopes and forced in scopes) else None

        if isinstance(slot.child, Terminal):
            if not dry:
                args.append(convert(slot.child.converter, operands[i],
                                    slot.name, usage))
            i += 1
            remaining -= 1
        else:
            child = slot.child
            child_args, i, _ = _fill(child, operands, i, chosen, given, usage,
                                     gate, positions, dry, reserved, scopes,
                                     force_key if chosen == 0 else None)
            if not dry:
                overlay = _claim_own(child, scopes)
                kwargs = _option_kwargs(child, given, usage, overlay,
                                        scopes)
                for t in child.slots:
                    if t.trailing:
                        # the child's own trailing arguments, from
                        # the shared end-reserve, in traversal order
                        kwargs[t.name] = convert(t.child.converter,
                                                 next(reserved),
                                                 t.name, usage)
                if child.callable is tuple:
                    # a tuple[...] slot: construct, don't call
                    args.append(tuple(child_args))
                elif child.callable is list:
                    # a list-default group: likewise
                    args.append(list(child_args))
                else:
                    args.append(child.callable(*child_args, **kwargs))
            remaining -= chosen

        if slot.barrier:
            # this group was certain to consume operands; now that
            # it's fed, options behind it become legal (gate rule)
            gate = i

    if scopes:
        scoped_window(scopes, [o.key for o in plan.options], 'out', i)
    return args, i, remaining


def scan(plan, argv, command_split=None):
    """
    Stage 1: the structural parse--token structure, option
    legality, argument counts, the gate rule--with zero user code.
    A malformed line dies here, before anything runs.  Returns
    (operands, given, rest, positions); rest is () unless
    command_split was given (see parse_tokens).
    """
    usage = plan.usage()
    options = {}
    for owner, o in all_options(plan):
        sibling = o.key in plan.sibling_keys
        marked = (not sibling
                  and (getattr(owner, 'windowed', False)
                       or o.key in plan.scoped_keys))
        for s in o.strings:
            entry = o.table_entry(windowed=marked)
            if sibling:
                # sibling-group keys record by announcement seq,
                # not operand position: the 's:' kinds
                entry = (entry[0], 's:' + entry[1]) + entry[2:]
            options[s] = entry
    if plan.pre_plan is not None:
        # the precommand's options scan in the same era
        for owner, o in all_options(plan.pre_plan):
            for s in o.strings:
                options[s] = o.table_entry()
    help_keys = help_option_strings(plan) if command_split is None else ()
    for s in help_keys:
        options[s] = ('--help', 'flag')
    positions = {}
    rest = ()
    if command_split is not None:
        operands, given, rest = parse_tokens(argv, options, usage,
                                             command_split=command_split,
                                             positions=positions)
    else:
        operands, given = parse_tokens(argv, options, usage,
                                       positions=positions)
    pre = plan.pre_plan
    if pre is None and getattr(plan.callable, 'appeal_precommand',
                               False):
        pre = plan      # no global command: the precommand IS the
                        # global plan
    if pre is not None:
        # the precommand acts at SCAN time, pinned like help:
        # program metadata (-V) outranks a malformed line and the
        # empty-line listing.  Its keys pop; stage 2 never sees
        # them.  A no-op when none of its options were given.
        pre_kwargs = _option_kwargs(pre, given, usage)
        for o in pre.options:
            given.pop(o.key, None)
        pre.callable(**pre_kwargs)
    if help_keys and given.get('--help'):
        # the auto-help option outranks a malformed line (pinned
        # order); a program's OWN --help is an ordinary option
        return operands, given, rest, positions
    n = len(operands)
    check_count(n, plan.minimum, plan.maximum, plan.valid_counts, usage)
    seen_names = {}
    for owner, o in all_options(plan):
        if (getattr(owner, 'windowed', False)
                or o.key in plan.scoped_keys):
            # windowed and scoped options bind per window--the
            # positional machinery owns their legality
            continue
        # (several rules may share a parameter: the string that
        # spoke last wins--ruled 2026-07-09--resolved at option-
        # kwargs time, no scan-stage repetition check anymore)
        if o.kind == 'group' and o.key in given:
            # greedy consumption grabbed up to the group's maximum;
            # the count grabbed must be one the group accepts
            child = o.child
            check_count(len(given[o.key]), child.minimum, child.maximum,
                        child.valid_counts, usage, what=f"option {o.key}",
                        param=o.key)
        if o.child is not None and o.key not in given:
            # (an absent option group: checked here so the error
            # lands in stage 1)
            # an absent option group: its inner options have
            # nothing to attach to (sibling keys excepted: another
            # window may claim them, or they summon the first)
            for inner in subtree_option_keys(o.child):
                if inner in given and inner not in plan.sibling_keys:
                    raise UsageError(
                        f"option {inner} requires {o.key}", usage)
    body = n - plan.tree_trailing
    scopes = _scopes_for(plan, given)
    _fill(plan, operands[:body], 0, body, given, usage, 0, positions,
          dry=True, scopes=scopes)
    scoped_resolve(scopes, usage)
    return operands, given, rest, positions


def run(plan, operands, given, positions=None, env=None):
    """
    Stage 2: build bottom-up and call--the conversions (user code)
    and the command itself.  Input is scan()'s output.  env is the
    execution environment: class-based commands stash the instance
    they construct (plan.constructs) and methods read their self
    from it (plan.binds).
    """
    usage = plan.usage()
    if help_option_strings(plan) and given.pop('--help', False):
        corpus = merge_docs(plan)
        print(render_help_page(usage, corpus, default_template,
                               margin=help_margin()), end='')
        return None
    n = len(operands)
    trailing = [s for s in plan.slots if s.trailing]

    # trailing arguments--anywhere in the tree--reserve their
    # count from the end of the stream; each converter takes its
    # own in traversal order (= stream order: inner before outer)
    kwargs = {}
    reserved = None
    if plan.tree_trailing:
        k = plan.tree_trailing
        reserved = iter(operands[-k:])
        operands = operands[:-k]
        n -= k

    scopes = _scopes_for(plan, given)
    if scopes:
        # the structural walk again, to place the scoped options
        # (scan did this too; a Processor could hand it over some
        # day, but recounting is cheap and self-contained)
        _fill(plan, operands, 0, n, given, usage, 0, None,
              dry=True, scopes=scopes)
        scoped_resolve(scopes, usage)
        scoped_rewind(scopes)
    args, i, remaining = _fill(plan, operands, 0, n, given, usage,
                               0, positions, reserved=reserved,
                               scopes=scopes)
    overlay = _claim_own(plan, scopes)
    for slot in trailing:
        kwargs[slot.name] = convert(slot.child.converter,
                                    next(reserved), slot.name, usage)
    assert remaining == 0 and i == len(operands), \
        f"automaton bug: consumed {i}/{len(operands)}"

    kwargs.update(_option_kwargs(
        plan, given, usage, overlay, scopes,
        sib=_sibling_scopes(plan, given, positions or {}, usage)))
    if plan.binds is not None and plan.constructs is not None:
        # a nested class: constructed via attribute access on the
        # parent instance, so a bound inner class composes without
        # Appeal knowing it exists
        parent = env[plan.binds]
        instance = getattr(parent, plan.name)(*args, **kwargs)
        env[plan.constructs] = instance
        return instance
    if plan.constructs is not None:
        # calling the class: __new__ and __init__ the ordinary
        # Python way.  The instance is the "return value"--diverted
        # into the environment, never mistaken for an exit status
        # (the early-exit contract only reads ints).
        instance = plan.callable(*args, **kwargs)
        if env is not None:
            env[plan.constructs] = instance
        return instance
    if plan.binds is not None:
        # a method command: self is the environment's instance
        return plan.callable(env[plan.binds], *args, **kwargs)
    return plan.callable(*args, **kwargs)


def parse(plan, argv, command_split=None):
    """
    Both stages, glued: scan, then run.  For a single command the
    two designs are indistinguishable--this is the fused
    convenience.  command_split makes this a global-command parse
    ahead of a subcommand (see parse_tokens); the return becomes
    (result, rest).
    """
    operands, given, rest, positions = scan(plan, argv, command_split)
    result = run(plan, operands, given, positions)
    if command_split is not None:
        return result, rest
    return result


def dispatch(commands, global_plan, argv, prog=None, repeat=False, help=True):
    """
    Rung 1 of the multi-command program: interpret the dispatch the
    same way the generated parse_command_set executes it.  commands
    maps command-word -> Plan.  repeat is Appeal's cycling: after a
    command's arguments--all of them--the next token may name
    another command.  help=False suppresses the automatic `help`
    command (v1's knob).
    """
    from .plan import command_set_usage
    from .help import summary, command_set_corpus
    entries = [(word, summary(plan.callable)) for word, plan in commands.items()]
    auto_help = help and 'help' not in commands
    usage_line = command_set_usage(prog or 'program', global_plan)
    corpus = command_set_corpus(global_plan, entries, auto_help)
    set_usage = render_command_listing(usage_line, corpus, default_template)
    command_words = frozenset(commands) | ({'help'} if auto_help else set())

    def listing():
        print(render_help_page(usage_line, corpus, default_template,
                               margin=help_margin()), end='')

    if help and argv and argv[0] in ('-h', '--help'):
        listing()
        return None

    invocations = []
    if global_plan is not None:
        # the full parse machinery, in command mode: the global's
        # operands end at the first operand naming a command (once
        # the minimum is satisfied; the maximum forces the split).
        # Stage 1 only--the global executes after the whole line
        # scans (parse-before-execute).
        split = (global_plan.minimum, global_plan.maximum, command_words)
        operands, given, rest, positions = scan(global_plan, argv, split)
        invocations.append((global_plan, operands, given, positions))
    else:
        rest = list(argv)

    if not rest:
        # orientation, not a diagnostic (ruled 2026-07-09)
        listing()
        return 1
    word = rest[0]
    if auto_help and word == 'help':
        # `help` alone: the listing; `help CMD`: CMD's --help;
        # `help help` describes itself (parity with the emitted
        # dispatcher)
        topic = rest[1] if len(rest) > 1 else None
        if topic == 'help':
            print('Print usage documentation on a specific command.')
            return None
        if topic:
            if topic not in commands:
                raise UsageError(
                    f"unknown command {topic!r}"
                    f"{did_you_mean(topic, commands)}", set_usage)
            return parse(commands[topic], ['--help'])
        listing()
        return None
    while rest:
        word = rest[0]
        plan = commands.get(word)
        if plan is None:
            raise UsageError(
                f"unknown command {word!r}"
                f"{did_you_mean(word, command_words)}", set_usage)
        split = None
        if repeat and plan.maximum is not None:
            # cycling boundary: greedy saturation, then the next
            # non-option token is the next command word
            split = (plan.maximum, plan.maximum, command_words)
        operands, given, rest, positions = scan(plan, rest[1:], split)
        invocations.append((plan, operands, given, positions))
        if not repeat:
            break
    # stage 2: execute left to right; the global's nonzero int
    # halts dispatch (v1's early-exit contract)
    result = None
    env = {}    # class commands' instances live here (coverage
                # found rung 1 never passed one: class trees
                # crashed in the reference implementation)
    for p, operands, given, positions in invocations:
        result = run(p, operands, given, positions, env)
        if (isinstance(result, int)
                and not isinstance(result, bool) and result):
            # the early-exit contract, every command in a cycle
            return result
    return result
