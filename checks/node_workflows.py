"""Installed CLI: Node exit outcomes and a scripted stale-state investigation.

The trial uses a synthetic fixture and a known fix, not a blind agent benchmark.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from integration import alive, redact, until, write_report


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True)
    parser.add_argument('--adapter', default=str(root / '.tools/js-debug/src/dapDebugServer.js'))
    options = parser.parse_args()
    cli, adapter = str(Path(options.cli).resolve()), str(Path(options.adapter).resolve())
    groups, transcript = [], []
    with tempfile.TemporaryDirectory(prefix='aidebug-node-workflows-') as directory:
        work = Path(directory)

        def call(*args):
            started = time.perf_counter()
            process = subprocess.run([cli, '--workspace', directory, '--timeout', '20', *args],
                                     cwd=work, capture_output=True, timeout=35)
            result = json.loads(process.stdout)
            transcript.append({'command': args, 'response': result, 'seconds': time.perf_counter() - started})
            assert process.returncode == 0 and not process.stderr and result['ok'], result
            return result

        def run(script, expected):
            started = time.perf_counter()
            process = subprocess.run(['node', str(script)], cwd=work, capture_output=True, text=True, timeout=10)
            transcript.append({'command': ['node', str(script)], 'exit_code': process.returncode,
                               'stdout': process.stdout, 'stderr': process.stderr,
                               'seconds': time.perf_counter() - started})
            assert process.returncode == expected, process.stderr
            return process

        def stop():
            result = call('stop')
            until(lambda: all(not alive(pid) for pid in result.get('processes', {}).values() if pid))
            assert not (work / '.aidebug/session.json').exists()

        def ended(result, expected):
            assert result['state'] in ('exited', 'terminated') and not result['timed_out'], result
            if result['target_exit_confirmed']:
                assert result['exit_code'] == expected, result
            else:
                assert result['state'] == 'terminated' and result['exit_code'] is None, result
                assert 'unknown' in result['diagnostics']['message'], result
            return result['target_exit_confirmed']

        try:
            for code in (0, 7):
                for expression in (f'process.exitCode = {code}', f'process.exit({code})'):
                    script = work / 'exit.js'
                    script.write_text(f'console.log("requested {code}"); {expression};\n')
                    run(script, code)
                    result = call('launch', '--adapter', adapter, '--run', 'exit.js')
                    ended(result, code)
                    time.sleep(.5)  # Allow a late exited event before classifying this adapter's result.
                    status = call('status')
                    assert status['state'] in ('exited', 'terminated'), status
                    if status['target_exit_confirmed']:
                        assert status['exit_code'] == code, status
                    else:
                        assert status['exit_code'] is None, status
                    assert f'requested {code}' in call('output')['output']['stdout']['text']
                    stop()
            groups.append('Node exitCode and explicit exit 0/7: confirmed code is exact or outcome remains unknown; separate runs verify codes')
            print('PASS', groups[-1], flush=True)

            source = (root / 'examples/javascript/restore.js').read_text()
            assignment = '  state.tabs = tabs;'
            assert source.count(assignment) == 1
            fixed = source.replace(assignment, "  if (state.active === workspace) state.tabs = tabs;")
            script = work / 'restore.js'
            measurements = {}

            # Compare repeatable command costs, not model reasoning speed.
            started = time.perf_counter()
            script.write_text(source)
            failure = run(script, 1)
            assert 'old-tab' in failure.stderr and 'new-tab' in failure.stderr
            instrumented = source.replace(assignment,
                "  console.error('TRIAL ' + JSON.stringify({workspace, active: state.active, tabs}));\n" + assignment)
            script.write_text(instrumented)
            logged = run(script, 1)
            observations = [json.loads(line[6:]) for line in logged.stderr.splitlines() if line.startswith('TRIAL ')]
            assert observations[-1] == {'workspace': 'old', 'active': 'new', 'tabs': ['old-tab']}, observations
            evidence_seconds = time.perf_counter() - started
            script.write_text(fixed)
            run(script, 0)
            measurements['test_and_log'] = {'evidence_seconds': evidence_seconds,
                'total_seconds': time.perf_counter() - started, 'commands': 3, 'temporary_logging_edits': 1}

            script.write_text(source)
            started = time.perf_counter()
            first = len(transcript)
            call('doctor', '--language', 'javascript', '--adapter', adapter)
            setup_seconds = time.perf_counter() - started
            line = source.splitlines().index(assignment) + 1
            result = call('launch', '--adapter', adapter, '--run', '--breakpoint', f'restore.js:{line}',
                          '--condition', "workspace === 'old'", 'restore.js')
            assert result['state'] == 'stopped' and result['location']['line'] == line, result
            assert any(bp['verified'] for bp in result['breakpoints']), result
            locals_by_name = {item['name']: item for item in result['locals']['items']}
            assert locals_by_name['workspace']['value'].strip("'\"") == 'old', result
            scopes = result['scopes']['items']
            # Find the actual module container, without adding snapshot locals to the target.
            state_scope = None
            for scope in scopes:
                if scope.get('expensive'):
                    continue
                variables = call('variables', scope['handle'])['variables']['items']
                if any(item['name'] == 'state' for item in variables):
                    state_scope = scope['handle']
                    break
            assert state_scope, scopes
            active = call('value', '--scope', state_scope, 'state.active')['value']['value']
            assert active.strip("'\"") == 'new', active
            tabs = call('variables', locals_by_name['tabs']['handle'])['variables']['items']
            assert any(item['value'].strip("'\"") == 'old-tab' for item in tabs), tabs
            evidence_seconds = time.perf_counter() - started
            ended(call('continue'), 1)
            failure_output = call('output')['output']['stderr']['text']
            assert 'AssertionError' in failure_output and 'old-tab' in failure_output, failure_output
            stop()
            script.write_text(fixed)
            # Verify the changed code in a fresh debug process, then verify its real exit independently.
            ended(call('launch', '--adapter', adapter, '--run', 'restore.js'), 0)
            success_output = call('output')['output']
            assert 'restoration passed' in success_output['stdout']['text'], success_output
            assert 'AssertionError' not in success_output['stderr']['text'], success_output
            stop()
            run(script, 0)
            measurements['debugger'] = {'setup_seconds': setup_seconds, 'evidence_seconds': evidence_seconds,
                'total_seconds': time.perf_counter() - started, 'commands': len(transcript) - first,
                'temporary_logging_edits': 0}
            transcript.append({'trial': measurements, 'kind': 'synthetic scripted replay; known cause and fix; no blind agent comparison'})
            groups.append('deterministic stale restoration: logs and live module state establish old/new mismatch; isolated fix passes fresh debugger and direct runs')
            print('PASS', groups[-1], flush=True)
            print(json.dumps(measurements, indent=2), flush=True)
        finally:
            stop()
            report = json.dumps({'passed': len(groups), 'failed': int(sys.exc_info()[0] is not None),
                'groups': groups, 'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                'platform': sys.platform, 'transcript': transcript}, indent=2)
            write_report(root / 'checks/node-workflows-results.json',
                         redact(report, [(directory, '<workspace>'), (str(root), '<repo>'), (str(Path.home()), '<home>')]))


if __name__ == '__main__':
    main()
