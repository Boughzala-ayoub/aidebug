"""Verify skill installation through the installed CLI, using isolated homes.

Run: python checks/skills.py --cli PATH/TO/aidebug
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from integration import redact, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--report", default="skills-results.json")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cli = str(Path(options.cli).resolve())
    groups, transcript, skipped = [], [], []
    versions = {"python": sys.version.split()[0], "platform": sys.platform}
    with tempfile.TemporaryDirectory(prefix="aidebug-skills-") as temporary:
        work = Path(temporary).resolve()
        home, project = work / "fake home", work / "project with spaces"
        home.mkdir()
        project.mkdir()
        environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        environment.pop("PYTHONPATH", None)
        targets = {"codex": home / ".agents/skills/aidebug", "claude-code": home / ".claude/skills/aidebug"}

        def call(*arguments, ok=True):
            process = subprocess.run([cli, "--workspace", str(project), *arguments],
                                     cwd=project, env=environment, capture_output=True, timeout=20)
            result = json.loads(process.stdout)
            transcript.append({"command": arguments, "response": result})
            assert not process.stderr and len(process.stdout) <= 65536, process.stderr
            assert result["ok"] == ok and process.returncode == (0 if ok else 1), result
            assert not (project / ".aidebug").exists(), "Skill setup started a debugging helper"
            return result

        def manage(action, *arguments, ok=True):
            return call("skills", action, *arguments, ok=ok)["skills"]

        def passed(name):
            groups.append(name)
            print("PASS", name, flush=True)

        both = ["--agent", "codex", "--agent", "claude-code"]
        codex = ["--agent", "codex"]
        target = targets["codex"]
        try:
            assert "skills" in call("--help")["help"]
            assert all(x["status"] == "not_installed" for x in manage("status"))
            assert not list(home.iterdir()), "Status must not create directories"
            for arguments in [("install",), ("uninstall",), ("install", "--agent", "unknown"), ("wrong",)]:
                assert call("skills", *arguments, ok=False)["error"]["code"] == "invalid_request"
            passed("JSON help, invalid requests, read-only status, no debug session")

            results = manage("install", *both)
            assert [x["status"] for x in results] == ["installed", "installed"], results
            for name, folder in targets.items():
                assert str(folder) == next(x["path"] for x in results if x["agent"] == name)
                skill = (folder / "SKILL.md").read_text()
                assert skill.startswith("---\nname: aidebug\ndescription: ")
                assert len(skill.split("---", 2)[1].split("description: ", 1)[1].strip()) <= 1024
                for reference in re.findall(r"\]\((references/[^)]+)\)", skill):
                    assert (folder / reference).is_file(), reference
                metadata = json.loads((folder / "installation.json").read_text())
                assert len(metadata["files"]) == 5 and metadata["owner"] == "aidebug-cli"
                versions["aidebug"] = metadata["version"]
            assert (target / "SKILL.md").read_bytes() == (targets["claude-code"] / "SKILL.md").read_bytes()
            timestamps = {f: f.stat().st_mtime_ns for f in target.rglob("*") if f.is_file()}
            assert all(x["status"] == "current" for x in manage("install", *both))
            assert timestamps == {f: f.stat().st_mtime_ns for f in timestamps}
            assert len(manage("status", *codex, *codex)) == 1
            passed("packaged skill and references, both user paths, idempotent installation")

            no_path = dict(environment, PATH="")
            fallback = subprocess.run([*metadata["cli_argv"], "--help"], cwd=project,
                                      env=no_path, capture_output=True, timeout=20)
            assert fallback.returncode == 0 and json.loads(fallback.stdout)["ok"]
            manifest = target / "installation.json"
            original_manifest = manifest.read_bytes()
            old = json.loads(original_manifest)
            old["version"] = "0.0.0"
            manifest.write_text(json.dumps(old))
            assert manage("status", *codex)[0]["status"] == "outdated"
            assert manage("install", *codex)[0]["status"] == "updated"
            assert manifest.read_bytes() == original_manifest
            old["cli_argv"][0] = str(work / "removed-python")
            manifest.write_text(json.dumps(old))
            assert not manage("status", *codex)[0]["recorded_python_exists"]
            assert manage("install", *codex)[0]["status"] == "updated"
            passed("CLI fallback without PATH, stale version and removed interpreter repair")

            skill_file = target / "SKILL.md"
            original_skill = skill_file.read_bytes()
            skill_file.write_bytes(original_skill + b"\nLocal customization\n")
            for action in ("status", "install", "uninstall"):
                assert manage(action, *codex, ok=False)[0]["error"]["code"] == "skill_conflict"
                assert skill_file.read_bytes().endswith(b"Local customization\n")
                assert manifest.read_bytes() == original_manifest
            skill_file.write_bytes(original_skill)
            skill_file.unlink()
            assert manage("uninstall", *codex, ok=False)[0]["status"] == "conflict"
            skill_file.write_bytes(original_skill)
            for invalid in [{"owner": "elsewhere"}, dict(old, cli_argv=[]), dict(old, files={"../outside": "a" * 64})]:
                manifest.write_text(json.dumps(invalid))
                assert manage("uninstall", *codex, ok=False)[0]["error"]["code"] == "skill_conflict"
                assert skill_file.read_bytes() == original_skill
            manifest.write_bytes(original_manifest)
            passed("edited and missing files preserved, invalid ownership and traversal rejected")

            sentinel = target / "my-notes.txt"
            sentinel.write_text("keep me")
            result = manage("uninstall", *codex)[0]
            assert result["unrelated_files_preserved"] and sentinel.read_text() == "keep me"
            assert not manifest.exists() and not skill_file.exists()
            assert manage("status", "--agent", "claude-code")[0]["status"] == "current"
            # The remaining directory is now user-owned, so a reinstall must refuse it.
            assert manage("install", *codex, ok=False)[0]["status"] == "conflict"
            sentinel.unlink()
            target.rmdir()
            assert manage("uninstall", *codex)[0]["status"] == "not_installed"
            assert manage("uninstall", "--agent", "claude-code")[0]["status"] == "uninstalled"
            assert all(not folder.exists() for folder in targets.values())
            passed("uninstall preserves unrelated files and other agent, repeat removal is safe")

            for name in ("AGENTS.md", "CLAUDE.md"):
                (project / name).write_text("Contributor instructions\n")
            assert all(x["status"] == "installed" for x in manage("install", *both, "--scope", "project"))
            assert (project / ".agents/skills/aidebug/SKILL.md").is_file()
            assert (project / ".claude/skills/aidebug/SKILL.md").is_file()
            assert all(x["status"] == "not_installed" for x in manage("status"))
            assert all(x["status"] == "uninstalled" for x in manage("uninstall", *both, "--scope", "project"))
            assert all((project / name).read_text() == "Contributor instructions\n" for name in ("AGENTS.md", "CLAUDE.md"))
            passed("project scope with spaces, separate user scope, contributor files untouched")

            outside = work / "outside"
            outside.mkdir()
            (outside / "sentinel").write_text("keep")
            try:
                target.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                if os.name != "nt":
                    raise
                junction = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "New-Item -ItemType Junction -Path $env:AIDEBUG_TEST_LINK -Target $env:AIDEBUG_TEST_TARGET -ErrorAction Stop | Out-Null"],
                    env=dict(environment, AIDEBUG_TEST_LINK=str(target), AIDEBUG_TEST_TARGET=str(outside)),
                    capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                assert junction.returncode == 0, junction.stderr
            try:
                for action in ("install", "uninstall", "status"):
                    assert manage(action, *codex, ok=False)[0]["error"]["code"] == "skill_conflict"
                assert sorted(f.name for f in outside.iterdir()) == ["sentinel"]
            finally:
                if target.is_symlink():
                    target.unlink()
                else:
                    target.rmdir()  # Remove the junction itself, never its destination.
            passed("symlink or junction redirects refused without touching external directory")
            assert all(x["status"] == "not_installed" for x in manage("status"))
        finally:
            report = json.dumps({"passed": len(groups), "failed": int(sys.exc_info()[0] is not None),
                                 "groups": groups, "versions": versions, "skipped": skipped, "transcript": transcript}, indent=2)
            report = redact(report, [(temporary, "<test>"), (str(root), "<repo>"), (str(Path.home()), "<home>")])
            write_report(root / "checks" / options.report, report)
    print(f"PASS {len(groups)} skill groups; skipped: {len(skipped)}")


if __name__ == "__main__":
    main()
