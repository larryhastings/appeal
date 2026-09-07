#!/usr/bin/env python3
#
# appeal/complete.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The build-time half of shell completion: turning a plan into the
# tables the engine answers from.  The engine itself--and the
# reentry protocol--live in appeal/__init__.py.

import os
import sys
from .backend import parse_short_options, is_option_token
from .frontend import all_options, help_option_strings
from . import AppealConfigurationError, UsageError
from .frontend import Terminal


_BASH_COMPLETION_SCRIPT = """\
_appeal_{ident}_completion() {{
    local IFS=$'\\n'
    COMPREPLY=( $( COMP_WORDS="${{COMP_WORDS[*]}}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   _APPEAL_COMPLETE=bash {prog} ) )
    return 0
}}
complete -o default -F _appeal_{ident}_completion {prog}
"""


_ZSH_COMPLETION_SCRIPT = """\
_appeal_{ident}_completion() {{
    local -a completions
    completions=("${{(@f)$(COMP_WORDS="${{(pj:\\n:)words}}" \\
                          COMP_CWORD=$((CURRENT-1)) \\
                          _APPEAL_COMPLETE=zsh {prog})}}")
    if (( ${{#completions}} )) && [ -n "${{completions[1]}}" ]; then
        compadd -a completions
    else
        _files
    fi
}}
compdef _appeal_{ident}_completion {prog}
"""


_FISH_COMPLETION_SCRIPT = """\
function _appeal_{ident}_completion
    set -lx _APPEAL_COMPLETE fish
    set -lx COMP_WORDS (commandline -co | string collect)
    set -lx COMP_CWORD (commandline -ct)
    set -l response ({prog})
    if set -q response[1]
        printf '%s\\n' $response
    else
        __fish_complete_path (commandline -ct)
    end
end
complete --command {prog} --no-files \\
    --arguments "(_appeal_{ident}_completion)"
"""


def _option_value_converters(o):
    "The converters for an option's operands, one per position."
    if len(o.converters) > 1:
        return tuple(o.converters[1:])
    return tuple(o.converters)


def completion_table(plan):
    """
    The completion table for one command (see
    complete_command for the shape).  Plain data plus
    converter references--plain data.
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


def completion_set_table(commands, global_plan, repeat=False,
                         sets=None):
    """
    The completion table for a multi-command program (see
    complete_command_set for the shape).  repeat: the root
    set cycles.  sets, if given, maps a parent word to
    {'commands': {sub: Plan}, 'repeat': bool}--a nested set.
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
    return {
        'commands': table,
        'global': dict(completion_table(global_plan), help=())
                  if global_plan is not None else None,
        'minimum': global_plan.minimum if global_plan is not None else 0,
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
                 repeat=False, sets=None):
    """
    Completion for a multi-command program: the global command's
    options and the command words before the command word; that
    command's completions after it--and under cycling, the
    resolution chain's words at each saturated boundary.
    """
    return complete_command_set(
        completion_set_table(commands, global_plan, repeat, sets),
        words, prefix)


def _completion_scan(options, words, maximum=None, boundary=None,
                     minimum=None):
    """
    A forgiving pass over the words already typed.  options maps
    each option string to (key, nargs, repeatable).  Returns
    (used, operands, pending, force_positional, leftover):
    pending is (key, nargs, remaining) when the cursor sits where
    an option's value belongs; leftover is the index where a new
    command word begins--at saturation (operands == maximum,
    cycling's boundary), or, for a set parent or the global
    command (minimum given), the first operand naming a boundary
    word once the minimum is met.  Options after saturation still
    belong to this command (the window stays open).  Never raises;
    completion must work on half-typed nonsense.
    """
    used = set()
    operands = 0
    pending = None
    force_positional = False
    # the short-option chars by arity, for parse_short_options: no
    # opargs (these bundle), exactly one, and two or more
    flag = set()
    oparg = set()
    opargs = set()
    for string, (key, nargs, repeatable) in options.items():
        if len(string) != 2:
            continue
        if not nargs:
            flag.add(string[1])
        elif nargs == 1:
            oparg.add(string[1])
        else:
            opargs.add(string[1])
    for index, word in enumerate(words):
        if pending:
            key, nargs, remaining = pending
            remaining -= 1
            pending = (key, nargs, remaining) if remaining else None
            continue
        if word == '--' and not force_positional:
            force_positional = True             # line-wide, like the parse
            continue
        if force_positional or not is_option_token(word, (flag, oparg, opargs)):
            # the scanner's own rule: a lone '-', and a negative number
            # that isn't a short bundle, are operands (Astra R11)
            if maximum is not None and operands >= maximum:
                return used, operands, pending, force_positional, index
            if (minimum is not None and boundary
                    and operands >= minimum and word in boundary):
                return used, operands, pending, force_positional, index
            operands += 1
            continue
        if word.startswith('--'):
            name = word.partition('=')[0]
            entry = options.get(name)
            if entry is None:
                continue
            key, nargs, repeatable = entry
            if not repeatable:
                used.add(name)
                used.add(key)
            if nargs and '=' not in word:
                pending = (key, nargs, nargs)
            continue
        # a short bundle: carve it with the parser's own
        # parse_short_options, so completion can never disagree with
        # the parse.  A bad token (unknown char, misplaced multi-value
        # option) gets no opinion at all--completion never raises.
        try:
            carved = list(parse_short_options(word, (flag, oparg, opargs)))
        except UsageError:
            continue
        for option, arg in carved:
            name = '-' + option
            key, nargs, repeatable = options[name]
            if not repeatable:
                used.add(name)
                used.add(key)
            if nargs and arg is None:           # nothing attached: the
                pending = (key, nargs, nargs)   # value is the next word(s)
    return used, operands, pending, force_positional, None


def _value_candidates(converter, prefix):
    """
    A value position's candidates: the expecting converter's
    completions callable, called with the prefix (a hint--the
    filter here is the belt).  No converter, or no completions
    attribute, means no opinion.  A wrong return type raises:
    garbage on TAB is a loud signal; silently dropping candidates
    hides the bug forever.
    """
    completions = getattr(converter, 'completions', None)
    if completions is None:
        return []
    values = completions(prefix)
    where = getattr(converter, '__name__', repr(converter))
    if not isinstance(values, tuple):
        raise AppealConfigurationError(
            f"completions for {where!r} returned "
            f"{type(values).__name__}, must return a tuple of str")
    out = []
    for value in values:
        if not isinstance(value, str):
            raise AppealConfigurationError(
                f"completions for {where!r} returned a "
                f"{type(value).__name__}, must return a tuple of str")
        if value.startswith(prefix):
            out.append(value)
    return sorted(out)


def _pending_candidates(table, pending, prefix):
    key, nargs, remaining = pending
    converters = table['values'].get(key) or ()
    index = nargs - remaining
    converter = converters[index] if index < len(converters) else None
    return _value_candidates(converter, prefix)


def _option_candidates(table, used, prefix):
    candidates = [s for s, (key, nargs, repeatable)
                  in table['options'].items()
                  if s.startswith(prefix)
                  and s not in used and key not in used]
    candidates.extend(s for s in table.get('help', ())
                      if s.startswith(prefix))
    return sorted(set(candidates))


def _operand_candidates(table, operands, prefix):
    slots = table['operands']
    converter = (slots[operands] if operands < len(slots)
                 else table['repeat'])
    return _value_candidates(converter, prefix)


def complete_command(table, words, prefix=''):
    """
    Candidate completions for one command.  table:

        options    {option string: (key, nargs, repeatable)}
        help       the automatic help option strings, or ()
        values     {key: (converter, ...)}, one per option operand
        operands   (converter-or-None, ...) in flat operand order
        repeat     the *args converter, or None
        minimum,   the argument-count arity (maximum None when
        maximum    unbounded)

    Options complete when the prefix starts with '-'; value
    positions ask the expecting converter; anything else is the
    shell's business (empty list = no opinion = filenames).
    """
    used, operands, pending, force_positional, _ = _completion_scan(
        table['options'], words)
    if pending:
        return _pending_candidates(table, pending, prefix)
    if prefix.startswith('-') and not force_positional:
        return _option_candidates(table, used, prefix)
    maximum = table.get('maximum')
    if maximum is not None and operands >= maximum:
        return []       # saturated: nothing more to say here
    return _operand_candidates(table, operands, prefix)


def complete_command_set(table, words, prefix=''):
    """
    Completion for a multi-command program.  table:

        commands   {word: command table, or a nested set entry
                    {'parent': table, 'commands': {...},
                     'repeat': bool}}
        global     the global command's table, or None
        minimum    the global command's minimum argument count
        repeat     the root set cycles

    The walk mirrors the dispatcher: the global command's portion,
    then commands--and under cycling, a saturated command's
    boundary offers the resolution chain's words, while its open
    window keeps offering its options.
    """
    commands = table['commands']
    words = list(words)

    def is_set(entry):
        return 'options' not in entry

    root_words = set(commands)

    stack = [{'commands': commands, 'repeat': table.get('repeat', False),
              'entered': False}]

    def resolvable():
        out = set()
        for depth, frame in enumerate(reversed(stack)):
            if (depth == 0 and not frame['entered']) or frame['repeat']:
                out.update(frame['commands'])
        return out

    index = 0
    g = table['global']
    if g is not None:
        used, operands, pending, forced, leftover = _completion_scan(
            g['options'], words, maximum=g.get('maximum'),
            boundary=root_words, minimum=table['minimum'])
        if leftover is None:
            if pending:
                return _pending_candidates(g, pending, prefix)
            if prefix.startswith('-') and not forced:
                return _option_candidates(g, used, prefix)
            if operands >= table['minimum']:
                out = sorted(w for w in root_words
                             if w.startswith(prefix))
                return out or _operand_candidates(g, operands, prefix)
            return _operand_candidates(g, operands, prefix)
        index = leftover

    while True:
        if index >= len(words):
            # the cursor sits at a command-word boundary
            if prefix.startswith('-'):
                return []
            return sorted(w for w in resolvable()
                          if w.startswith(prefix))
        word = words[index]
        entry = target = None
        for depth, frame in enumerate(reversed(stack)):
            if (depth == 0 and not frame['entered']) or frame['repeat']:
                if word in frame['commands']:
                    entry, target = frame['commands'][word], frame
                    break
        if entry is None:
            return []           # half-typed nonsense: no opinion
        while stack[-1] is not target:
            stack.pop()
        target['entered'] = True
        index += 1
        if is_set(entry):
            # a nested parent: a global command of its own little
            # set, flexible boundary
            stack.append({'commands': entry['commands'],
                          'repeat': entry.get('repeat', False),
                          'entered': False})
            parent = entry['parent']
            sub_words = resolvable()
            used, operands, pending, forced, leftover = (
                _completion_scan(parent['options'], words[index:],
                                 maximum=parent.get('maximum'),
                                 boundary=sub_words,
                                 minimum=parent.get('minimum', 0)))
            if leftover is None:
                if pending:
                    return _pending_candidates(parent, pending, prefix)
                if prefix.startswith('-') and not forced:
                    return _option_candidates(parent, used, prefix)
                if operands >= parent.get('minimum', 0):
                    out = sorted(w for w in sub_words
                                 if w.startswith(prefix))
                    return out or _operand_candidates(parent, operands,
                                                      prefix)
                return _operand_candidates(parent, operands, prefix)
            index += leftover
            continue
        # a leaf command: greedy saturation is its boundary when
        # anything could follow
        chain = resolvable()
        used, operands, pending, forced, leftover = _completion_scan(
            entry['options'], words[index:],
            maximum=entry.get('maximum') if chain else None)
        if leftover is None:
            if pending:
                return _pending_candidates(entry, pending, prefix)
            if prefix.startswith('-') and not forced:
                return _option_candidates(entry, used, prefix)
            maximum = entry.get('maximum')
            if (chain and maximum is not None
                    and operands >= maximum):
                # saturated: the resolution chain's words complete
                # here (the open window's options handled above)
                return sorted(w for w in chain
                              if w.startswith(prefix))
            return _operand_candidates(entry, operands, prefix)
        index += leftover


def completion_script(shell, prog):
    """
    The shell function that wires `prog <TAB>` to the reentry
    protocol.  Source its output (or install it in the shell's
    completion directory).  The shell's own filename completion is
    the fallback when the program has no opinion: bash via
    `-o default`, zsh via `_files`, fish via __fish_complete_path.
    (zsh needs compsys loaded--the standard
    `autoload -U compinit && compinit`.)

    Every courier serializes the words the shell is completing as a
    NEWLINE-joined string (a newline can't occur inside a single
    command-line word), so arguments that contain spaces survive
    intact--the reentry splits on newlines, not whitespace.  prog
    is shell-quoted so an odd or hostile program name can't break
    (or inject into) the sourced script.
    """
    import shlex
    ident = ''.join(c if c.isalnum() else '_' for c in prog)
    prog = shlex.quote(prog)
    if shell == 'bash':
        return _BASH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    if shell == 'zsh':
        return _ZSH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    if shell == 'fish':
        return _FISH_COMPLETION_SCRIPT.format(ident=ident, prog=prog)
    raise AppealConfigurationError(
        f"unsupported completion shell {shell!r} "
        f"(supported: 'bash', 'zsh', 'fish')")


def _split_arg_string(string):
    """
    Split a command line into words the way a shell would, tolerating
    an unterminated quote or escape at the very end--the word the
    user is mid-typing (`prog "New Yo<TAB>`), which a strict parse
    would reject.  The partial token is kept as-is.

    (This is Click's split_arg_string, adopted: the shell hands the
    reentry its word array with the quote CHARACTERS still attached,
    so a plain split would leave `"New York"` quoted; a shell lexer
    recovers the logical value.  shlex is stdlib.)
    """
    import shlex
    lex = shlex.shlex(string, posix=True)
    lex.whitespace_split = True
    lex.commenters = ''
    out = []
    try:
        out.extend(lex)
    except ValueError:
        # end-of-string mid-quote/escape: keep the partial token
        # (it's in lex.token, not yet emitted)
        out.append(lex.token)
    return out


def completion_reentry(completer, prog):
    """
    Answer an _APPEAL_COMPLETE reentry, if this invocation is one:
    prints the candidates (or the sourcing script) and returns an
    exit code.  Returns None if this isn't a reentry.  The caller
    gates on empty argv--the shell's data travels in COMP_WORDS /
    COMP_CWORD, never argv, so a real reentry is always a bare
    invocation.
    """
    import os
    mode = os.environ.get('_APPEAL_COMPLETE')
    if not mode:
        return None
    if mode.startswith('source'):
        shell = mode.partition('_')[2] or 'bash'
        print(completion_script(shell, prog))
        return 0
    # The couriers NEWLINE-join the shell's words so each stays its
    # own line; but the shell hands them over with the QUOTE
    # CHARACTERS still attached ("New York" arrives as one word,
    # literally quoted), so we run the reconstructed line back
    # through a shell lexer to recover the logical value (New York,
    # unquoted).  _split_arg_string tolerates the half-typed current
    # word's unterminated quote.  Because each shell word is on its
    # own line, the lexer's tokens line up with the shell's own word
    # count, so COMP_CWORD still indexes them.
    raw = os.environ.get('COMP_WORDS', '')
    words = _split_arg_string(raw)
    if mode == 'fish':
        # fish can't cheaply produce a word INDEX; its courier
        # sends the current token's TEXT in COMP_CWORD instead
        # (commandline -ct: empty when the cursor follows a space).
        prefix = os.environ.get('COMP_CWORD', '')
        if prefix:
            prefix = _split_arg_string(prefix)[0]
        before = words[1:]
        if prefix and before and before[-1] == prefix:
            before = before[:-1]
    else:
        # bash and zsh answer identically: the courier normalizes
        # the shell's own variables into COMP_WORDS/COMP_CWORD
        # (zsh's 1-based CURRENT becomes 0-based COMP_CWORD in
        # the courier).  A cword past the last token--the cursor
        # sits at a fresh, empty word--yields an empty prefix.
        try:
            cword = int(os.environ.get('COMP_CWORD', '0') or 0)
        except ValueError:
            cword = 0
        before = words[1:cword]
        prefix = words[cword] if 0 <= cword < len(words) else ''
    for candidate in completer(before, prefix):
        print(candidate)
    return 0


