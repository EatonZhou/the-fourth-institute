"""Token-free ISA client for a RunScript process."""
import json
import os
from management_common.local_ipc import connect_local


def inputs():
    with open(os.environ['PORTAL_SCRIPT_INPUT'], encoding='utf-8') as stream:
        return json.load(stream)


def set_output(fields):
    if not isinstance(fields, dict):
        raise ValueError('输出必须是 JSON 对象')
    data = json.dumps(fields, ensure_ascii=False, allow_nan=False).encode()
    if len(data) > 65536:
        raise ValueError('输出不能超过 64 KB')
    with open(os.environ['PORTAL_SCRIPT_OUTPUT'], 'wb') as stream:
        stream.write(data)


def isa(name, **arguments):
    payload = json.dumps({'instruction': name, 'arguments': arguments}).encode() + b'\n'
    with connect_local(os.environ['PORTAL_ISA_SOCKET'], timeout=120) as client:
        client.sendall(payload)
        with client.makefile('rb') as stream:
            result = json.loads(stream.readline(8 * 1024 * 1024))
    if result.get('status') != 'committed':
        raise RuntimeError(json.dumps(result.get('error', {}), ensure_ascii=False))
    return result.get('result')


def cli(output=False):
    import sys
    values = {}
    for value in sys.argv[1 if output else 2:]:
        key, separator, raw = value.partition('=')
        if not separator or not key:
            raise ValueError('参数必须是 名称=值')
        try:
            values[key] = json.loads(raw)
        except ValueError:
            values[key] = raw
    if output:
        set_output(values)
    else:
        print(json.dumps(isa(sys.argv[1], **values), ensure_ascii=False))
