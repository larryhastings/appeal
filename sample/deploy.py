#!/usr/bin/env python3
# A tiny deployer: its own errors are Markdown, like Appeal's.
import appeal
from appeal import UsageError, CommandError, escaped

app = appeal.Appeal(name='deploy', version='1.0')

REGIONS = ('us', 'eu')


@app.command()
def push(build, *, region='us', force=False):
    """
    Push a build to a region.

    # Arguments
    build
    : The build to push, like `web-1234`.
    # Options
    region
    : One of `us` or `eu`.
    """
    if region not in REGIONS:
        # [region]{.option} and [build]{.argument} name THIS command's
        # parameters; Appeal renders them as the usage line does.  The
        # value came from the user: escaped() makes it safe in prose (a
        # code span would show it as typed, and escapes nothing inside)
        raise UsageError(f"[region]{{.option}} must be one of `us` or `eu`, "
                         f"not `{escaped(region)}`, to push [build]{{.argument}}")
    if build.endswith('-broken') and not force:
        # a failure after parsing: no usage line, just the message
        raise CommandError(f"`{build}` **failed its checks**; "
                           f"[force]{{.option}} pushes it anyway", exit_code=3)
    print(f"pushed {build} to {region}")


if __name__ == '__main__':
    app.main()
