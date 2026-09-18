"""Exercise installed CLI help examples through real Windows PowerShell 5.1."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from integration import alive, redact, until, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True)
    options = parser.parse_args()
    assert os.name == 'nt', 'This check requires Windows PowerShell'
    cli = str(Path(options.cli).resolve())
    root = Path(__file__).resolve().parents[1]
    groups, transcript = [], []
    shell_version = None
    with tempfile.TemporaryDirectory(prefix='aidebug shell ') as directory:
        work = Path(directory)
        executable = work / 'debug tools' / 'aidebug.exe'
        executable.parent.mkdir()
        shutil.copyfile(cli, executable)
        (work / 'app.py').write_text("import sys\nname = 'ready'\narguments = sys.argv[1:]\nprint(arguments)\n")
        # Use the exact commands shipped in help, replacing only the install path.
        help_result = subprocess.run([cli, 'launch', '--help'], capture_output=True, check=True)
        examples = [line.strip() for line in json.loads(help_result.stdout)['help'].splitlines()
                    if line.strip().startswith('& ')]
        assert len(examples) == 2, examples
        examples = [line.replace('C:/debug tools/aidebug.exe', str(executable).replace("'", "''")) for line in examples]
        script = work / 'check.ps1'
        script.write_text("$ErrorActionPreference = 'Stop'\n" +
            "Write-Output $PSVersionTable.PSVersion.ToString()\n" +
            '\n'.join(examples) + '\n', encoding='ascii')
        environment = os.environ.copy()
        environment.pop('VIRTUAL_ENV', None)
        try:
            process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script)],
                                     cwd=work, env=environment, capture_output=True, timeout=60)
            assert process.returncode == 0 and not process.stderr, (process.stdout, process.stderr)
            lines = process.stdout.decode().splitlines()
            shell_version = lines[0]
            assert shell_version.startswith('5.1.'), shell_version
            stopped, changed = map(json.loads, lines[1:])
            transcript.extend([stopped, changed])
            assert stopped['ok'] and stopped['state'] == 'stopped' and stopped['location']['line'] == 4, stopped
            assert changed['ok'] and changed['breakpoints'][0]['condition'] == "name == 'ready'", changed
            assert stopped['breakpoints'][0]['condition'] == "name == 'ready'", stopped
            end = subprocess.run([cli, '--workspace', directory, 'continue'], capture_output=True, check=True, timeout=30)
            assert json.loads(end.stdout)['exit_code'] == 0
            output = subprocess.run([cli, '--workspace', directory, 'output'], capture_output=True, check=True)
            assert "['two words']" in json.loads(output.stdout)['output']['stdout']['text']
            groups.append('PowerShell 5.1 help commands: spaced executable path, quoted launch and updated conditions, spaced target argument')
            print('PASS', groups[-1], flush=True)
        finally:
            closed = subprocess.run([cli, '--workspace', directory, 'stop'], capture_output=True, timeout=20)
            result = json.loads(closed.stdout)
            assert result['ok'], result
            until(lambda: all(not alive(pid) for pid in result.get('processes', {}).values() if pid))
            report = json.dumps({
                'passed': len(groups), 'failed': int(sys.exc_info()[0] is not None),
                'groups': groups, 'powershell': shell_version, 'transcript': transcript}, indent=2)
            write_report(root / 'checks/powershell-results.json',
                         redact(report, [(directory, '<workspace>'), (str(Path.home()), '<home>')]))


if __name__ == '__main__':
    main()
