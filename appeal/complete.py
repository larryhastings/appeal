#!/usr/bin/env python3
#
# appeal/complete.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The build-time half of shell completion: turning a plan into the
# tables the engine answers from.  The engine itself--and the
# reentry protocol--lives in runtime.py, streamed into every
# standalone script, so completion works identically in-process
# and in a generated script.

from .build import all_options, help_option_strings
from .plan import Terminal
from .runtime import complete_command, complete_command_set


def _option_value_converters(o):
    "The converters for an option's operands, one per position."
    if len(o.converters) > 1:
        return tuple(o.converters[1:])
    return tuple(o.converters)


def completion_table(plan):
    """
    The completion table for one command (see
    runtime.complete_command for the shape).  Plain data plus
    converter references--everything a generated script can carry.
    """
    options = {}
    values = {}
    for owner, o in all_options(plan):
        entry = o.table_entry(windowed=getattr(owner, 'windowed', False))
        kind = entry[1]
        base = kind[2:] if kind[:2] in ('w:', 's:') else kind
        if base in ('flag', 'nullary'):
            # flags consume nothing; a flag entry's [2] is the
            # value presence stores, never an operand count
            nargs = 0
        elif len(entry) > 3:
            # folds and groups carry (minimum, maximum); the parser
            # consumes greedily to the maximum, so completion does too
            nargs = entry[3]
        else:
            nargs = entry[2] if len(entry) > 2 else 1
        repeatable = (base in ('multi', 'fold')
                      or kind[:2] in ('w:', 's:'))
        for s in o.strings:
            options[s] = (o.key, nargs, repeatable)
        if nargs:
            values[o.key] = _option_value_converters(o)

    operands = []
    repeat = [None]

    def terminal_count(p):
        n = 0
        for s in p.slots:
            n += 1 if isinstance(s.child, Terminal) else terminal_count(s.child)
        return n

    def walk(p):
        for s in p.slots:
            child = s.child
            if isinstance(child, Terminal):
                if s.repeat:
                    repeat[0] = child.converter
                else:
                    operands.append(child.converter)
                continue
            # a nonterminal that carries completions speaks for its
            # sole operand: `hue: color` completes with color's
            # completions, even though color's grammar wraps a str
            # terminal.  (Build validation guarantees the "sole".)
            if (getattr(child.callable, 'completions', None) is not None
                    and terminal_count(child) == 1):
                if s.repeat:
                    repeat[0] = child.callable
                else:
                    operands.append(child.callable)
                continue
            walk(child)

    walk(plan)
    return {
        'options': options,
        'help': tuple(help_option_strings(plan)),
        'values': values,
        'operands': tuple(operands),
        'repeat': repeat[0],
        'minimum': plan.minimum,
        'maximum': plan.maximum,
    }


# the auto version command's completion entry: a word that takes
# nothing--zero operands, no options
_VERSION_ENTRY = {
    'options': {}, 'help': (), 'values': {}, 'operands': (),
    'repeat': None, 'minimum': 0, 'maximum': 0,
    }


def completion_set_table(commands, global_plan, repeat=False,
                         sets=None, auto_version=False, help=True):
    """
    The completion table for a multi-command program (see
    runtime.complete_command_set for the shape).  repeat: the root
    set cycles.  sets, if given, maps a parent word to
    {'commands': {sub: Plan}, 'repeat': bool}--a nested set.
    auto_version: the program supplies the automatic version
    command, so the word completes.  help=False suppresses the
    automatic `help` command (v1's knob).
    """
    sets = sets or {}

    def entry_for(word, plan):
        # recursive: sets is flat (every parent, any depth), so a
        # child that is itself a parent nests its own entry
        spec = sets.get(word)
        if spec is None:
            return completion_table(plan)
        return {
            'parent': completion_table(plan),
            'commands': {sub: entry_for(sub, p)
                         for sub, p in spec['commands'].items()},
            'repeat': spec.get('repeat', False),
        }

    table = {}
    for word, plan in commands.items():
        table[word] = entry_for(word, plan)
    if auto_version:
        table['version'] = dict(_VERSION_ENTRY)
    return {
        'commands': table,
        'global': dict(completion_table(global_plan), help=())
                  if global_plan is not None else None,
        'minimum': global_plan.minimum if global_plan is not None else 0,
        'auto_help': help and 'help' not in commands,
        'repeat': repeat,
    }


def completions(plan, words, prefix=''):
    """
    Candidate completions for `prefix`, given the `words` already
    typed.  Options complete on a '-' prefix; value positions ask
    the expecting converter's `completions`; anything else is the
    shell's business (empty list = no opinion = filenames).
    """
    return complete_command(completion_table(plan), words, prefix)


def completions_set(commands, global_plan, words, prefix='',
                 repeat=False, sets=None, auto_version=False, help=True):
    """
    Completion for a multi-command program: the global command's
    options and the command words before the command word; that
    command's completions after it--and under cycling, the
    resolution chain's words at each saturated boundary.
    """
    return complete_command_set(
        completion_set_table(commands, global_plan, repeat, sets,
                             auto_version, help=help),
        words, prefix)
