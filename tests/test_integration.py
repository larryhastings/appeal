#!/usr/bin/env python3
#
# tests/test_integration.py
# Integration tests (Larry, 2026-09-10): whole programs, shaped like
# real ones, driven end to end through app.main--every drive pins the
# exit code, stdout, stderr, and what ran.  The rest of the suite
# verifies mostly from the inside (corpus shapes, engine states); the
# bugs found on 2026-09-10 (nesting never rendered, group options
# undecorated, complex off the leaf list, Literal, async, an unwrapped
# usage trailer, a <SELF> operand in a method command's usage) were all
# invisible to it.  These pages are what a user sees.  3.8+ (Literal).

from big import test
from big.builtin import load

load('appeal')

import contextlib
import io
import pathlib
import sys
import typing

import appeal
from appeal import Appeal, verbatim


def tron_program(config=None):
    """A tron-shaped program: commands, subcommands, config, passthrough."""
    log = []
    app = Appeal(name='tron', version='2.1', config=config, stylesheet=False,
                 doc="Manage containers.\n\nEvery command respects `--verbose`.")

    @app.precommand()
    def tron(*, verbose=False, jobs: int = 1):
        """
        # Options
        verbose
        : Say what's happening.
        jobs
        : How many things to do at once.
        """
        log.append(('tron', verbose, jobs))

    @app.command()
    def status():
        "Show what's running."
        log.append('status')

    @app.command()
    def run(*args: verbatim):
        """
        Run a command inside the container.

        Everything after `run` goes to the container untouched.
        """
        log.append(('run', list(args)))

    @app.command()
    def deploy(target, *, region, dry_run=False):
        """
        Deploy to a target.

        # Arguments
        target
        : Which deployment.
        # Options
        region
        : The region to deploy into.
        dry_run
        : Plan only.
        """
        log.append(('deploy', target, region, dry_run))

    db_command = app.command('db')
    @app.command()
    def db(*, url='postgres://localhost'):
        """
        Database things.

        # Options
        url
        : Where the database lives.
        """
        log.append(('db', url))

    @db_command.command()
    def start(port: int = 5432):
        "Start the database."
        log.append(('start', port))

    @db_command.command()
    def stop(*, force=False):
        "Stop the database."
        log.append(('stop', force))

    @db_command.default()
    def db_status():
        log.append('db-status')

    @app.command('sync-all', restriction='deprecated')
    def sync_all():
        "Sync everything (the old spelling of sync)."
        log.append('sync-all')

    @app.command(restriction='hidden')
    def dump_plan():
        "Developer switch."
        log.append('dump-plan')

    return app, log


def dl_program():
    """A downloader: a global-only program, no commands."""
    log = []
    app = Appeal(name='dl', version='0.3', stylesheet=False)

    @app.precommand()
    def dl(url, *more: str, out: pathlib.Path = pathlib.Path('.'),
           quality: typing.Literal['best', 'worst'] = 'best',
           retries: int = 3, quiet=False):
        """
        Download videos.

        Each URL is fetched in turn; `--out` names the directory.

        # Arguments
        url
        : The first video.
        more
        : Any others.
        # Options
        out
        : Where to put the files.
        quality
        : Which rendition to fetch.
        retries
        : Give up after this many failures.
        quiet
        : No progress output.
        """
        log.append((url, list(more), out, quality, retries, quiet))

    return app, log


def tool_program():
    """A class-as-app with a nested subcommand class and cycling."""
    log = []
    app = Appeal(name='tool', repeat=True, stylesheet=False)

    @app.app()
    class Tool:
        """
        A tool built from a class.

        # Options
        verbose
        : Say more.
        """
        def __init__(self, *, verbose=False):
            self.verbose = verbose
            log.append(('Tool', verbose))

        @app.command()
        def add(self, a: int, b: int):
            "Add two numbers."
            log.append(('add', a + b, self.verbose))

        @app.command()
        class Db:
            """
            A database, named.

            # Arguments
            name
            : Which database.
            """
            db_command = app.command('Db')

            def __init__(self, name):
                self.name = name
                log.append(('Db', name))

            @db_command.command()
            def wipe(self, *, really=False):
                "Wipe it."
                log.append(('wipe', self.name, really))

    return app, log


def drive(app, argv):
    "One run through app.main: (exit code, stdout, stderr)."
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            app.main(list(argv))
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
    return code, out.getvalue(), err.getvalue()


def check(make, drives, **kw):
    app, log = make(**kw)
    for argv, code, out, err, ran in drives:
        log.clear()
        got = drive(app, argv)
        assert got == (code, out, err), (argv, got)
        assert log == ran, (argv, log)
    return app


TRON_CONFIG = {'jobs': 4, 'deploy': {'region': 'eu'}, 'db': {'url': 'postgres://db'}}

TRON = [
    ([], 1,
     """\
usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     '',
     [('tron', False, 4)]),
    (['-h'], 0,
     """\
usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     '',
     []),
    (['help', 'db'], 0,
     """\
usage: tron db [-h|--help] [-u|--url <URL>] <COMMAND>

Database things.

Options
-------

-u|--url <URL>  Where the database lives.

Subcommands
-----------

start  Start the database.
stop   Stop the database.
""",
     '',
     [('tron', False, 4)]),
    (['db', 'stop', '-h'], 0,
     """\
usage: tron db stop [-h|--help] [-f|--force]

Stop the database.

Options
-------

-f|--force
""",
     '',
     []),
    (['--version'], 0,
     """\
2.1
""",
     '',
     []),
    (['version'], 0,
     """\
2.1
""",
     '',
     [('tron', False, 4)]),
    (['-v', 'status'], 0,
     '',
     '',
     [('tron', True, 4), 'status']),
    (['run', 'ls', '-la', '--', '-h'], 0,
     '',
     '',
     [('tron', False, 4), ('run', ['ls', '-la', '--', '-h'])]),
    (['run', '-h'], 0,
     """\
usage: tron run [-h|--help] [<ARGS>]...

Run a command inside the container.

Everything after run goes to the container untouched.

Arguments
---------

<ARGS>
""",
     '',
     []),
    (['deploy', 'prod'], 0,
     '',
     '',
     [('tron', False, 4), ('deploy', 'prod', 'eu', False)]),
    (['--jobs', '2', 'deploy', '--region', 'us', 'prod', '--dry-run'], 0,
     '',
     '',
     [('tron', False, 2), ('deploy', 'prod', 'us', True)]),
    (['db'], 0,
     '',
     '',
     [('tron', False, 4), ('db', 'postgres://db'), 'db-status']),
    (['db', 'start', '5433'], 0,
     '',
     '',
     [('tron', False, 4), ('db', 'postgres://db'), ('start', 5433)]),
    (['db', '--url', 'x', 'stop', '--force'], 0,
     '',
     '',
     [('tron', False, 4), ('db', 'x'), ('stop', True)]),
    (['db', 'psuh'], 2,
     '',
     """\
error: unknown command 'psuh' of 'tron db'

usage: tron db [-h|--help] [-u|--url <URL>] <COMMAND>

Database things.

Options
-------

-u|--url <URL>  Where the database lives.

Subcommands
-----------

start  Start the database.
stop   Stop the database.
""",
     []),
    (['bogus'], 2,
     '',
     """\
error: unknown command 'bogus'

usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     []),
    (['stauts'], 2,
     '',
     """\
error: unknown command 'stauts' (did you mean 'status'?)

usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     []),
    (['sync-all'], 0,
     '',
     """\
warning: command 'sync-all' is deprecated
""",
     [('tron', False, 4), 'sync-all']),
    (['dump-plan'], 0,
     '',
     '',
     [('tron', False, 4), 'dump-plan']),
    (['--jobs', 'many', 'status'], 2,
     '',
     """\
error: invalid value for 'jobs': 'many' (invalid literal for int() with base 10: 'many')

usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     []),
    (['status', 'extra'], 2,
     '',
     """\
error: unexpected argument 'extra'

usage: tron status [-h|--help]
""",
     []),
    (['--loud', 'status'], 2,
     '',
     """\
error: unknown option '--loud'

usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     []),
    (['help', 'deploy'], 0,
     """\
usage: tron deploy [-h|--help] -r|--region <REGION> [-d|--dry-run] <TARGET>

Deploy to a target.

Arguments
---------

<TARGET>  Which deployment.

Options
-------

-r|--region <REGION>  The region to deploy into.
-d|--dry-run          Plan only.
""",
     '',
     [('tron', False, 4)]),
    (['deploy', '--region', 'eu', 'prod', '-v'], 2,
     '',
     """\
error: option '-v' can't be used here; it goes before the command

usage: tron [-h|--help [<TOPIC>]] [--version] [-v|--verbose] [-j|--jobs <JOBS>]
            <COMMAND>

Manage containers.

Every command respects --verbose.

Options
-------

-v|--verbose      Say what's happening.
-j|--jobs <JOBS>  How many things to do at once.

Commands
--------

status    Show what's running.
run       Run a command inside the container.
deploy    Deploy to a target.
db        Database things.
sync-all  Sync everything (the old spelling of sync).  (deprecated)
version   Print the program's version.
help      Print usage documentation on a specific command.
""",
     []),
]

DL = [
    (['-h'], 0,
     """\
usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...

Download videos.

Each URL is fetched in turn; --out names the directory.

Arguments
---------

<URL>   The first video.
<MORE>  Any others.

Options
-------

-o|--out <OUT>          Where to put the files.
-q|--quality <QUALITY>  Which rendition to fetch.
-r|--retries <RETRIES>  Give up after this many failures.
--quiet                 No progress output.
""",
     '',
     []),
    (['http://a/1'], 0,
     '',
     '',
     [('http://a/1', [], pathlib.Path('.'), 'best', 3, False)]),
    (['http://a/1', 'http://a/2', '-o', '/tmp/v', '--quality', 'worst', '-r', '5', '--quiet'], 0,
     '',
     '',
     [('http://a/1', ['http://a/2'], pathlib.Path('/tmp/v'), 'worst', 5, True)]),
    ([], 2,
     '',
     """\
error: missing argument 'url'

usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...
""",
     []),
    (['http://a/1', '--quality', 'medium'], 2,
     '',
     """\
error: invalid value for 'quality': 'medium' (must be one of 'best', 'worst')

usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...
""",
     []),
    (['http://a/1', '--retries', 'x'], 2,
     '',
     """\
error: invalid value for 'retries': 'x' (invalid literal for int() with base 10: 'x')

usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...
""",
     []),
    (['--', '-weird-url'], 0,
     '',
     '',
     [('-weird-url', [], pathlib.Path('.'), 'best', 3, False)]),
    (['http://a/1', '--bogus'], 2,
     '',
     """\
error: unknown option '--bogus' (did you mean '--out'?)

usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...
""",
     []),
    (['--version'], 0,
     """\
0.3
""",
     '',
     []),
    (['http://a/1', '-q'], 2,
     '',
     """\
error: option '-q' requires a value

usage: dl [-h|--help] [--version] [-o|--out <OUT>] [-q|--quality <QUALITY>]
          [-r|--retries <RETRIES>] [--quiet] <URL> [<MORE>]...
""",
     []),
]

TOOL = [
    ([], 1,
     """\
usage: tool [-h|--help [<TOPIC>]] [-v|--verbose] <COMMAND>

A tool built from a class.

Options
-------

-v|--verbose  Say more.

Commands
--------

add   Add two numbers.
Db    A database, named.
help  Print usage documentation on a specific command.
""",
     '',
     [('Tool', False)]),
    (['-h'], 0,
     """\
usage: tool [-h|--help [<TOPIC>]] [-v|--verbose] <COMMAND>

A tool built from a class.

Options
-------

-v|--verbose  Say more.

Commands
--------

add   Add two numbers.
Db    A database, named.
help  Print usage documentation on a specific command.
""",
     '',
     []),
    (['-v', 'add', '1', '2'], 0,
     '',
     '',
     [('Tool', True), ('add', 3, True)]),
    (['add', '1', '2', 'add', '3', '4'], 0,
     '',
     '',
     [('Tool', False), ('add', 3, False), ('add', 7, False)]),
    (['Db', 'main', 'wipe', '--really'], 0,
     '',
     '',
     [('Tool', False), ('Db', 'main'), ('wipe', 'main', True)]),
    (['Db', 'main'], 0,
     '',
     '',
     [('Tool', False), ('Db', 'main')]),
    (['help', 'Db'], 0,
     """\
usage: tool Db [-h|--help] <NAME> <COMMAND>

A database, named.

Arguments
---------

<NAME>  Which database.

Subcommands
-----------

wipe  Wipe it.
""",
     '',
     [('Tool', False)]),
    (['Db', 'main', 'wipe', '-h'], 0,
     """\
usage: tool Db wipe [-h|--help] [-r|--really]

Wipe it.

Options
-------

-r|--really
""",
     '',
     []),
    (['help', 'add'], 0,
     """\
usage: tool add [-h|--help] <A> <B>

Add two numbers.

Arguments
---------

<A>
<B>
""",
     '',
     [('Tool', False)]),
    (['add', 'x', '2'], 2,
     '',
     """\
error: invalid value for 'a': 'x' (invalid literal for int() with base 10: 'x')

usage: tool add [-h|--help] <A> <B>
""",
     [('Tool', False)]),
    (['Db'], 2,
     '',
     """\
error: missing argument 'name'

usage: tool Db [-h|--help] <NAME> <COMMAND>
""",
     []),
]


def test_tron_shaped_program():
    # commands, subcommands with a default, config for the tree, a
    # required option satisfied by config, verbatim passthrough, a
    # deprecated and a hidden command, every kind of error trailer
    app = check(tron_program, TRON, config=TRON_CONFIG)
    # completion: hidden commands never offered; the verbatim run has
    # no opinion; a command's own options
    assert app.complete([], '') == ['db', 'deploy', 'help', 'run', 'status', 'sync-all', 'version']
    assert app.complete([], '-') == ['--jobs', '--verbose', '-j', '-v']
    assert app.complete(['db'], '') == ['start', 'stop']
    assert app.complete(['deploy'], '-') == ['--dry-run', '--help', '--region', '-d', '-h', '-r']
    assert app.complete(['run', 'x'], '-') == []
    # the schema knows the required option and the hidden command
    described = app.schema('appeal', '1.0')
    assert 'dump-plan' not in described['commands']
    (region,) = [o for o in described['commands']['deploy']['options'] if o['name'] == 'region']
    assert region['required'] is True
    # without the config, the required option is missing
    app.config = {}
    code, out, err = drive(app, ['deploy', 'prod'])
    assert code == 2 and err.startswith("error: missing option '--region'\n\nusage: tron deploy"), err
    # a bad config key names itself, wearing the overview
    app.config = {'nope': 1}
    code, out, err = drive(app, ['status'])
    assert code == 2 and err.startswith("error: config: 'nope' isn't an option here\n\nusage: tron "), err


def test_downloader_shaped_program():
    # a global-only program: no commands, a leaf Path, a Literal choice,
    # `--` before a dash-leading operand, did-you-mean for an option,
    # the wrapped usage trailer
    app = check(dl_program, DL)
    assert app.complete([], '-') == ['--help', '--out', '--quality', '--quiet', '--retries', '-h', '-o', '-q', '-r']
    assert app.complete(['u', '--quality'], '') == ['best', 'worst']
    assert app.complete(['u', '--quality'], 'w') == ['worst']
    described = app.schema('appeal', '1.0')
    assert [o['name'] for o in described['operands']] == ['url', 'more']


def test_class_shaped_program():
    # class-as-app: __init__ is the global command, methods are
    # commands, a nested class is a subcommand set constructed with
    # the parent's instance, cycling runs several commands on one
    # instance; no <SELF> anywhere on the pages
    app = check(tool_program, TOOL)
    instances = app.process(['-v', 'Db', 'main', 'wipe']).instances
    assert [(getattr(c, '__name__', c), type(i).__name__) for c, i in instances] == \
        [(None, 'NoneType'), (None, 'Tool'), ('Db', 'Db'), ('wipe', 'NoneType')]
    assert instances[1][1].verbose is True and instances[2][1].name == 'main'


def run_tests(run=None):
    (run or test.run)(name='appeal integration', module=__name__)


if __name__ == "__main__":
    run_tests()
    test.finish()
