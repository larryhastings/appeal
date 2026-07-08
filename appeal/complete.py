#!/usr/bin/env python3
#
# appeal/complete.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# Shell-completion groundwork: given the words before the cursor and
# the partial word at it, what could legally come next?  The plan
# tree knows.  This is the in-process API; shell integration scripts
# (bash/zsh completion functions calling back into the program) come
# later and will sit on top of it.
#
# Conventions: candidates are offered for option strings (when the
# partial word starts with '-') and for command words (in a command
# set, at the command position).  Operand values belong to the
# shell's own completion (usually filenames), so an empty list means
# "no opinion", not "nothing is legal".

from .build import all_options, help_option_strings
from .plan import Terminal


def _scan(plan, words):
    """
    A forgiving pass over the words already typed: how many operands
    appeared, which single-occurrence options were used, and whether
    the cursor sits where an option's value belongs.  Never raises;
    completion must work on half-typed nonsense.
    """
    table = {}
    for owner, o in all_options(plan):
        entry = o.table_entry(windowed=getattr(owner, 'windowed', False))
        kind = entry[1]
        base = kind[2:] if kind.startswith('w:') else kind
        nargs = entry[2] if len(entry) > 2 else (0 if base == 'flag' else 1)
        repeatable = base in ('multi', 'fold') or kind.startswith('w:')
        for s in o.strings:
            table[s] = (o.key, nargs, repeatable)

    used = set()
    operands = 0
    expect_values = 0
    force_positional = False
    for word in words:
        if expect_values:
            expect_values -= 1
            continue
        if force_positional or not word.startswith('-') or word == '-':
            operands += 1
            continue
        if word == '--':
            force_positional = True
            continue
        name = word.partition('=')[0] if word.startswith('--') else word[:2]
        entry = table.get(name)
        if entry is None:
            continue
        key, nargs, repeatable = entry
        if not repeatable:
            used.add(name if word.startswith('--') else key)
            used.add(key)
        if nargs and '=' not in word:
            expect_values = nargs
    return table, used, operands, expect_values, force_positional


def complete(plan, words, prefix=''):
    """
    Candidate completions for `prefix`, given the `words` already
    typed.  Options complete when prefix starts with '-'; operand
    values are the shell's business (empty list = no opinion).
    """
    table, used, operands, expect_values, force_positional = _scan(plan, words)
    if expect_values or force_positional:
        return []
    if not prefix.startswith('-'):
        return []
    candidates = [s for s, (key, nargs, repeatable) in table.items()
                  if s.startswith(prefix)
                  and s not in used and key not in used]
    candidates.extend(s for s in help_option_strings(plan)
                      if s.startswith(prefix))
    return sorted(set(candidates))


def complete_set(commands, global_plan, words, prefix=''):
    """
    Completion for a multi-command program.  Before the command
    word: the global command's options, and command names (plus
    'help') at the command position.  After it: that command's
    completions.
    """
    auto_help = 'help' not in commands
    command_words = set(commands) | ({'help'} if auto_help else set())

    # find the command word among the words already typed
    if global_plan is not None:
        table, used, operands, expect_values, force_positional = (
            _scan(global_plan, words))
        minimum = global_plan.minimum
    else:
        minimum = 0
    for index, word in enumerate(words):
        if word in command_words and not word.startswith('-'):
            name = word if word in commands else None
            if word == 'help':
                # completing a help topic: the command names
                remaining = words[index + 1:]
                if not remaining and not prefix.startswith('-'):
                    return sorted(w for w in command_words
                                  if w.startswith(prefix))
                if name is None:
                    return []
            if name is not None:
                return complete(commands[name], words[index + 1:], prefix)

    # still in global territory
    if global_plan is not None:
        table, used, operands, expect_values, force_positional = (
            _scan(global_plan, words))
        if expect_values:
            return []
        if prefix.startswith('-'):
            return sorted(set(
                s for s, (key, nargs, repeatable) in table.items()
                if s.startswith(prefix)
                and s not in used and key not in used))
        if operands >= minimum:
            return sorted(w for w in command_words if w.startswith(prefix))
        return []
    if prefix.startswith('-'):
        return []
    return sorted(w for w in command_words if w.startswith(prefix))
