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
    UsageError, accumulate, call_converter, collect_mapping, convert, fold,
    parse_tokens, check_count, default_templates, render_command_listing,
    render_help_page, window_options,
    )


def _feasible(remaining, suffix):
    counts, minimum = suffix
    if counts is None:
        return remaining >= minimum
    return remaining in counts


def _option_kwargs(plan, given, usage):
    "Resolve a rule's own options from the parse's given table."
    kwargs = {}
    defaults = {}
    given_names = set()
    for o in plan.options:
        key = o.key
        if key not in given:
            if o.child is not None:
                # an option group that wasn't given: its inner
                # options have nothing to attach to
                for inner in subtree_option_keys(o.child):
                    if inner in given:
                        raise UsageError(
                            f"option {inner} requires {key}", usage)
            # several rules may share a parameter (per-declaration
            # @app.option); an absent rule must not clobber a given
            # sibling--defaults fill in a second pass
            if not o.kwargs_delivered:
                defaults.setdefault(o.name, o.default)
            continue
        if o.name in given_names:
            raise UsageError(
                f"option {key} specified more than once", usage)
        given_names.add(o.name)
        if o.kind == 'flag':
            kwargs[o.name] = True
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
            child_args, _, _ = _fill(child, operands, 0, len(operands),
                                     given, usage)
            kwargs[o.name] = child.callable(
                *child_args, **_option_kwargs(child, given, usage))
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


def _fill(plan, operands, i, remaining, given, usage,
          gate=0, positions=None):
    """
    The counting automaton, interpreted: fill this plan's
    non-trailing slots from operands[i:], consuming exactly
    `remaining` operands.  Returns (positional_args, i, remaining).

    gate/positions implement the gate rule: this plan's options
    may not have appeared before `gate` operands were consumed
    (i.e., before every required group to its left was fed).
    """
    if gate and positions and not plan.certain:
        # the gate rule applies only to skippable groups: a certain
        # group's options float free (v1, probed)
        for o in plan.options:
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
                for j in range(take):
                    args.append(convert(slot.child.converter, operands[i + j],
                                        slot.name, usage))
                i += take
                remaining -= take
                continue
            # a converter group: `take` operands split into fixed-
            # size instances; options bind to instances by window
            child = slot.child
            k = child.minimum
            if take % k:
                raise UsageError(
                    f"wrong number of arguments for {slot.name!r}: "
                    f"each takes {k}, {take % k} left over", usage)
            count = take // k
            occurrences = {o.key: given[o.key]
                           for o in child.options if o.key in given}
            givens = window_options(occurrences, i, k, count,
                                    slot.name, usage, gate=gate)
            for j in range(count):
                child_args, i, _ = _fill(child, operands, i, k, {}, usage)
                kwargs = _option_kwargs(child, givens[j], usage)
                args.append(child.callable(*child_args, **kwargs))
            remaining -= take
            continue

        for c in slot.count_options:
            if c <= remaining and _feasible(remaining - c, slot.suffix_after):
                chosen = c
                break
        else:   # pragma: no cover -- check_count makes this unreachable
            raise UsageError(f"can't fill {slot.name!r}", usage)

        if chosen == 0 and not slot.required:
            # v1 semantics: giving one of a group's options FORCES
            # the group--it is entered with zero operands, defaults
            # and all.  Only a group that *needs* operands it can't
            # have makes the option an error.
            forced = False
            for key in subtree_option_keys(slot.child) if not isinstance(slot.child, Terminal) else ():
                if key in given:
                    if slot.child.minimum == 0:
                        forced = True
                        break
                    raise UsageError(
                        f"option {key} requires {slot.name!r}", usage)
            if not forced:
                args.append(slot.default)
                continue
            # forced: fall through and enter the group, chosen == 0

        if isinstance(slot.child, Terminal):
            args.append(convert(slot.child.converter, operands[i],
                                slot.name, usage))
            i += 1
            remaining -= 1
        else:
            child = slot.child
            child_args, i, _ = _fill(child, operands, i, chosen, given, usage,
                                     gate, positions)
            kwargs = _option_kwargs(child, given, usage)
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

    return args, i, remaining


def parse(plan, argv, command_split=None):
    """
    Parse argv against the plan and invoke the command.
    Semantics must match the generated parser exactly.

    command_split makes this a global-command parse ahead of a
    subcommand (see parse_tokens); the return value becomes
    (result, rest).
    """
    usage = plan.usage()

    options = {}
    for owner, o in all_options(plan):
        for s in o.strings:
            options[s] = o.table_entry(windowed=getattr(owner, 'windowed', False))

    help_keys = help_option_strings(plan) if command_split is None else ()
    for s in help_keys:
        options[s] = ('--help', 'flag')

    positions = {}
    rest = None
    if command_split is not None:
        operands, given, rest = parse_tokens(argv, options, usage,
                                             command_split=command_split,
                                             positions=positions)
    else:
        operands, given = parse_tokens(argv, options, usage,
                                       positions=positions)
    if help_keys and given.pop('--help', False):
        corpus = merge_docs(plan)
        print(render_help_page(usage, corpus, default_templates), end='')
        return None
    n = len(operands)
    check_count(n, plan.minimum, plan.maximum, plan.valid_counts, usage)

    trailing = [s for s in plan.slots if s.trailing]

    # trailing required operands reserve their count from the end
    kwargs = {}
    if trailing:
        k = len(trailing)
        reserved = operands[-k:]
        operands = operands[:-k]
        n -= k
        for slot, text in zip(trailing, reserved):
            kwargs[slot.name] = convert(slot.child.converter, text,
                                        slot.name, usage)

    args, i, remaining = _fill(plan, operands, 0, n, given, usage,
                               0, positions)
    assert remaining == 0 and i == len(operands), \
        f"automaton bug: consumed {i}/{len(operands)}"

    kwargs.update(_option_kwargs(plan, given, usage))

    result = plan.callable(*args, **kwargs)
    if command_split is not None:
        return result, rest
    return result


def dispatch(commands, global_plan, argv, prog=None):
    """
    Rung 1 of the multi-command program: interpret the dispatch the
    same way the generated parse_command_set executes it.  commands
    maps command-word -> Plan.
    """
    from .plan import command_set_usage
    from .help import summary, command_set_corpus
    entries = [(word, summary(plan.callable)) for word, plan in commands.items()]
    auto_help = 'help' not in commands
    usage_line = command_set_usage(prog or 'program', global_plan)
    corpus = command_set_corpus(global_plan, entries, auto_help)
    set_usage = render_command_listing(usage_line, corpus, default_templates)
    command_words = frozenset(commands) | ({'help'} if auto_help else set())

    def listing():
        print(render_help_page(usage_line, corpus, default_templates), end='')

    if auto_help and argv and argv[0] in ('-h', '--help'):
        listing()
        return None

    if global_plan is not None:
        # the full parse machinery, in command mode: the global's
        # operands end at the first operand naming a command (once
        # the minimum is satisfied; the maximum forces the split)
        split = (global_plan.minimum, global_plan.maximum, command_words)
        result, rest = parse(global_plan, argv, command_split=split)
        if isinstance(result, int) and not isinstance(result, bool) and result:
            return result
    else:
        rest = list(argv)

    if not rest:
        raise UsageError("no command specified.", set_usage)
    word = rest[0]
    if auto_help and word == 'help':
        # `help` alone: the command listing; `help CMD`: CMD's --help
        topic = rest[1] if len(rest) > 1 else None
        if topic and topic != 'help':
            if topic not in commands:
                raise UsageError(f"unknown command {topic!r}", set_usage)
            return parse(commands[topic], ['--help'])
        listing()
        return None
    plan = commands.get(word)
    if plan is None:
        raise UsageError(f"unknown command {word!r}", set_usage)
    return parse(plan, rest[1:])
