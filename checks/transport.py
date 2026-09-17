"""Bounded DAP transport check: authentication, fragmented frames and response order."""

import sys
import threading
import time

from aidebug.dap import DAP, DebugError
from aidebug.session import Session, source_breakpoints


# The fake adapter must reject its own deliberately unauthenticated connection
# before its real connection can send any DAP requests. Actual adapters are covered
# by integration.py and multilanguage.py.
ADAPTER = r'''
import json, socket, sys, time
port = int(sys.argv[sys.argv.index("--connect") + 1])
token = sys.argv[sys.argv.index("--auth-token") + 1]
with socket.create_connection(("127.0.0.1", port), timeout=4) as wrong:
    wrong.sendall(b"Auth-Token: wrong\r\n")
    assert wrong.recv(1) == b"", "Unauthenticated client was accepted"
sock = socket.create_connection(("127.0.0.1", port), timeout=4)
sock.sendall(f"Auth-Token: {token}\r\n".encode())
stream = sock.makefile("rb")
def read():
    length = int(stream.readline().split(b":")[1])
    assert stream.readline() == b"\r\n"
    return json.loads(stream.read(length))
def send(message):
    body = json.dumps(message, ensure_ascii=False).encode()
    data = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    for start in range(0, len(data), 7):
        sock.sendall(data[start:start + 7])
first, second = read(), read()
send({"seq": 1, "type": "event", "event": "initialized"})
for sequence, request in enumerate([second, first], 2):
    send({"seq": sequence, "type": "response", "request_seq": request["seq"],
          "command": request["command"], "success": True, "body": {"value": request["command"] + " é"}})
print("Content-Length: this is target output", flush=True)
errors = [
    {'message': 'notStopped', 'body': {'error': {'id': 9234,
      'format': 'Cannot inspect {frame}; missing {unknown}', 'variables': {'frame': 'outer {unknown}'}}}},
    {'message': 'Plain adapter error'},
    {'body': None},
    {'message': 'Fallback error', 'body': {'error': 'invalid detail'}},
]
for sequence, error in enumerate(errors, 4):
    request = read()
    send({'seq': sequence, 'type': 'response', 'request_seq': request['seq'],
          'command': request['command'], 'success': False, **error})
time.sleep(30)
'''


def main():
    try:
        source_breakpoints({7: "x == 1"}, {})
    except DebugError as error:
        assert error.code == "unsupported"
    else:
        raise AssertionError("An adapter without conditional-breakpoint support must reject the request")
    events, failures, output = [], [], []
    captured = threading.Event()

    def capture(category, value):
        output.append((category, value))
        captured.set()

    dap = DAP(lambda name, body: events.append(name), failures.append, failures.append,
              command=[sys.executable, "-c", ADAPTER], connect_back=True, capture=capture)
    try:
        deadline = time.monotonic() + 5
        first, second = dap.send("first"), dap.send("second")
        assert dap.wait(first, deadline)["value"] == "first é"
        assert dap.wait(second, deadline)["value"] == "second é"
        assert "initialized" in events and not failures, (events, failures)
        assert captured.wait(2)
        assert any(category == "stdout" and "this is target output" in value for category, value in output), output
        for index, message in enumerate(('Cannot inspect outer {unknown}; missing {unknown}',
                                        'Plain adapter error', 'Debug adapter rejected request', 'Fallback error')):
            try:
                dap.request('error', {}, time.monotonic() + 5)
            except DebugError as error:
                expected = {'code': 'adapter_failure', 'message': message}
                if index == 0:
                    expected['adapter_error_id'] = 9234
                assert error.as_dict() == expected, error.as_dict()
                session = Session()
                session.state = 'running'
                session.failure(error)
                assert session.status()['error'] == expected
            else:
                raise AssertionError('Expected an adapter error')
        assert len(DebugError('adapter_failure', 'x' * 600, 7).as_dict()['message']) == 512
    finally:
        dap.close()
    assert dap.process.poll() is not None
    print("PASS authenticated adapter connection, fragmented UTF-8 frames, out-of-order responses, structured error details, events, output isolation and cleanup")


if __name__ == "__main__":
    main()
