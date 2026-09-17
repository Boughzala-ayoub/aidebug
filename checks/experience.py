"""Installed CLI: scope/value access, responsive waits, and diagnostics.

Run: python checks/experience.py --cli PATH/TO/aidebug
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from integration import alive, until, redact, write_report
from aidebug.cli import encode, fields, project_fields
from aidebug.session import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True)
    parser.add_argument('--report', default='experience-results.json')
    options = parser.parse_args()
    cli = str(Path(options.cli).resolve())
    root = Path(__file__).resolve().parents[1]
    groups, transcript = [], []
    with tempfile.TemporaryDirectory(prefix='aidebug-experience-') as directory:
        work = Path(directory)
        environment = os.environ.copy()
        environment.pop('VIRTUAL_ENV', None)
        (work / 'scope.py').write_text('global_data = {"nested": {"count": 731}}\n'
            'def investigate():\n    local_value = 42\n    print(local_value)\ninvestigate()\n')
        (work / 'busy.py').write_text('import time\nwhile True:\n    time.sleep(0.02)\n')

        def call(*args, ok=True):
            process = subprocess.run([cli, '--workspace', directory, *args], env=environment,
                cwd=directory, capture_output=True, timeout=40)
            result = json.loads(process.stdout)
            transcript.append({'command': args, 'response': result})
            assert result['ok'] == ok and process.returncode == (0 if ok else 1), result
            assert not process.stderr and len(process.stdout) <= 65536, process.stderr
            return result

        def passed(name):
            groups.append(name)
            print('PASS', name, flush=True)

        def start_wait():
            return subprocess.Popen([cli, '--workspace', directory, '--timeout', '300', 'wait'],
                                    cwd=directory, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def finish_wait(process, expected):
            stdout, stderr = process.communicate(timeout=8)
            result = json.loads(stdout)
            assert not stderr and result['state'] == expected and not result['timed_out'], result

        try:
            session = Session()
            session.state, session.thread = 'stopped', 1
            requests = []
            replies = {'threads': {'threads': [{'id': 1}]},
                       'stackTrace': {'stackFrames': [{'id': 1, 'name': 'example', 'line': 1}]},
                       'scopes': {'scopes': [{'name': 'Locals', 'variablesReference': 1}]},
                       'variables': {'variables': []}}
            def request(command, arguments, deadline):
                requests.append(command)
                return replies[command]
            session.request = request
            for command in ('status', 'inspect', 'scopes'):
                requests.clear()
                session.execute({'command': command, 'timeout': 1})
                assert requests == ['threads', 'stackTrace', 'scopes', 'variables'], requests
            passed('each inspection fetches scopes once, without caching across commands')
            sample = {'ok': True, 'error': {'code': 'example', 'message': 'retain'},
                      'groups': [{'rows': [{'value': '\u2014 \u273d', 'truncated': True}]}]}
            projected = project_fields(sample, fields('groups[].rows[].value'))
            assert projected == sample and encode(projected).isascii()
            assert json.loads(encode(projected)) == sample
            assert project_fields({'rows': []}, fields('rows[].value')) == {'rows': []}
            assert project_fields({}, fields('rows[].value')) == {'rows': None}
            assert call('doctor')['runs_program'] is False
            assert not (work / '.aidebug').exists()
            assert call('doctor', '--language', 'javascript', '--adapter', 'missing.js', ok=False)['error']['code'] == 'missing_dependency'
            assert call('--timeout', '3601', 'wait', ok=False)['error']['code'] == 'invalid_request'
            passed('doctor validates tools without launch; invalid deadlines return JSON')

            launch = call('launch', '--run', '--breakpoint', 'scope.py:4', 'scope.py')
            scopes = call('scopes')['scopes']['items']
            globals_scope = next(s for s in scopes if s['name'] == 'Globals')
            value = call('value', '--scope', globals_scope['handle'], 'global_data.nested.count')['value']
            assert value['value'] == '731', value
            assert call('value', '--scope', globals_scope['handle'], 'global_data', '--key', 'nested', '--key', 'count')['value']['value'] == '731'
            missing = call('value', '--scope', globals_scope['handle'], 'global_data.missing', ok=False)
            assert missing['error']['code'] == 'value_not_found'
            assert 'Path component 2' in missing['error']['message'] and 'variables ' in missing['error']['message']
            parent = missing['error']['message'].split('variables ')[1].split('.')[0]
            assert any(item['name'].strip("'\"") == 'nested' for item in call('variables', parent)['variables']['items'])
            assert call('--fields', 'value.value', 'value', '--scope', globals_scope['handle'], 'global_data.nested.count') == {'ok': True, 'value': {'value': '731', 'truncated': False}}
            projected = call('--fields', 'state,missing', 'status')
            assert projected == {'ok': True, 'state': 'stopped', 'missing': None}, projected
            projected = call('--fields', 'scopes.items[].name,scopes.items[].handle', 'scopes')
            assert projected['scopes']['items'] == [{'name': s['name'], 'handle': s['handle'], 'truncated': s['truncated']} for s in scopes], projected
            assert set(projected) == {'ok', 'scopes'} and projected['scopes']['truncated'] is False
            projected = call('--fields', 'state', 'value', '--scope', globals_scope['handle'], 'global_data.missing', ok=False)
            assert projected['error']['code'] == 'value_not_found' and 'session' not in projected
            for invalid in ('', 'stack..name', 'state,state', 'stack,stack.frames', 'stack.frames,stack', 'stack.frames,stack[].name', 'state.' * 9,
                            'rows[0]', 'x' * 2049, ','.join('x' + str(i) for i in range(33))):
                assert call('--fields', invalid, 'stop', ok=False)['error']['code'] == 'invalid_request'
            assert call('status')['state'] == 'stopped', 'Invalid fields must not execute stop'
            record = (work / '.aidebug/recordings' / (launch['session'] + '.jsonl')).read_text(encoding='utf-8')
            results = [json.loads(line) for line in record.splitlines()]
            assert any(item.get('response', {}).get('session') == launch['session'] and item.get('command') == 'scopes' for item in results)
            passed('field projection preserves JSON arrays, errors and bounds; invalid selectors have no effects; recordings retain full results')
            assert call('continue')['target_exit_confirmed']
            call('value', '--scope', globals_scope['handle'], 'global_data', ok=False)
            call('stop')
            until(lambda: all(not alive(p) for p in launch['processes'].values() if p))
            passed('real Python globals, nested paths, exact keys, missing values, expired handles and cleanup')

            launch = call('--fields', 'outcome,timed_out', 'launch', '--run', '--no-wait', 'busy.py')
            assert launch['outcome'] == 'started' and not launch['timed_out']
            assert launch == {'ok': True, 'outcome': 'started', 'timed_out': False, 'breakpoints_truncated': False}, launch
            until(lambda: call('status')['phase'] == 'ready')
            result = call('--fields', 'state,timed_out,outcome', '--timeout', '0.1', 'wait')
            assert result['timed_out'] and result['state'] == 'running' and result['outcome'] == 'deadline'
            pending = start_wait()
            time.sleep(0.5)
            for _ in range(20):
                assert call('--timeout', '0.1', 'wait', ok=False)['error']['code'] == 'busy'
            assert call('status')['state'] == 'running'
            assert call('pause')['state'] == 'stopped'
            finish_wait(pending, 'stopped')
            call('--timeout', '0.1', 'continue')
            pending = start_wait()
            time.sleep(0.5)
            call('stop')
            finish_wait(pending, 'exited')
            assert not (work / '.aidebug/session.json').exists()
            passed('asynchronous launch, deadlines, duplicate waits rejected without exhausting control slots, pause and stop interrupt waits')
        finally:
            closed = call('stop')
            until(lambda: all(not alive(p) for p in closed.get('processes', {}).values() if p))
            report = json.dumps({'passed': len(groups), 'failed': int(sys.exc_info()[0] is not None),
                'groups': groups, 'python': sys.version.split()[0], 'platform': sys.platform, 'transcript': transcript}, indent=2)
            write_report(root / 'checks' / options.report, redact(report, [(directory, '<workspace>'), (str(root), '<repo>'), (str(Path.home()), '<home>')]))


if __name__ == '__main__':
    main()
