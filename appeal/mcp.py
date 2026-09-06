#!/usr/bin/env python3
#
# appeal/mcp.py
# Part of Appeal v2.
# Copyright 2021-2026 by Larry Hastings
#
# The MCP server: JSON-RPC 2.0 over stdio, newline-delimited--the
# Model Context Protocol's stdio transport, stdlib only.  The tool
# schemas it serves come from appeal/schema.py (the generic
# machinery; this module is one consumer of it).

from . import AppealError
from .schema import _MCP_VERSIONS      # the protocol revisions this
                                       # server speaks, oldest to newest;
                                       # [-1] is offered when the client
                                       # asks for one we don't


def run_mcp(tools, name, version='0'):
    """
    Serve this program's commands as MCP tools: JSON-RPC 2.0 over
    stdio, newline-delimited--the Model Context Protocol's stdio
    transport, stdlib only.  tools maps a tool name to
    (description, input_schema, call) where call takes the
    arguments mapping and returns the result.

    One bad call never ends the session (hardened 2026-09-03, the
    Sol review): a tool call that raises an Appeal error--bad data,
    a command's own CommandError--answers as a tool result marked
    isError, which the model can read and react to; an unexpected
    exception answers as a JSON-RPC internal error; malformed or
    non-object requests answer as protocol errors.  In every case
    the loop keeps serving.

    Runs until stdin closes.  Returns 0.
    """
    import json
    import sys

    def reply(id, result=None, error=None):
        message = {'jsonrpc': '2.0', 'id': id}
        if error is not None:
            message['error'] = error
        else:
            message['result'] = result
        sys.stdout.write(json.dumps(message) + '\n')
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            # a parse error answers with id null (JSON-RPC 2.0)--
            # silence would leave the client waiting forever
            reply(None, error={'code': -32700, 'message': 'parse error'})
            continue
        if not isinstance(request, dict):
            # valid JSON that isn't a request object ([], 5, "x"...)
            reply(None, error={'code': -32600,
                               'message': 'request must be an object'})
            continue
        method = request.get('method', '')
        id = request.get('id')
        if method == 'initialize':
            params = request.get('params')
            requested = (params.get('protocolVersion')
                         if isinstance(params, dict) else None)
            reply(id, {
                # the spec's negotiation: echo the requested version
                # when we speak it, otherwise offer the latest we
                # do--never rubber-stamp a version we've never met
                'protocolVersion': (requested
                                    if requested in _MCP_VERSIONS
                                    else _MCP_VERSIONS[-1]),
                'capabilities': {'tools': {}},
                'serverInfo': {'name': name, 'version': version},
            })
        elif method == 'notifications/initialized':
            pass
        elif method == 'ping':
            reply(id, {})
        elif method == 'tools/list':
            reply(id, {'tools': [
                {'name': tool, 'description': description,
                 'inputSchema': schema}
                for tool, (description, schema, call)
                in sorted(tools.items())]})
        elif method == 'tools/call':
            params = request.get('params')
            if not isinstance(params, dict):
                reply(id, error={'code': -32602,
                                 'message': 'params must be an object'})
                continue
            tool = tools.get(params.get('name'))
            if tool is None:
                reply(id, error={'code': -32602,
                                 'message': f"unknown tool "
                                            f"{params.get('name')!r}"})
                continue
            arguments = params.get('arguments') or {}
            if not isinstance(arguments, dict):
                reply(id, error={'code': -32602,
                                 'message': 'arguments must be an object'})
                continue
            description, schema, call = tool
            try:
                result = call(arguments)
            except AppealError as e:
                # an EXPECTED failure--bad data, the command's own
                # CommandError--is a tool result the model reads and
                # reacts to, not a protocol error
                reply(id, {'content': [{'type': 'text',
                                        'text': str(e)}],
                           'isError': True})
                continue
            except Exception as e:
                # an unexpected bug in the command: report it and
                # keep serving
                reply(id, error={'code': -32603,
                                 'message': f'{type(e).__name__}: {e}'})
                continue
            reply(id, {'content': [{'type': 'text',
                                    'text': '' if result is None
                                            else str(result)}]})
        elif id is not None:
            reply(id, error={'code': -32601,
                             'message': f'unknown method {method!r}'})
    return 0
