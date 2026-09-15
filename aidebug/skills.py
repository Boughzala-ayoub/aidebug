"""Install the bundled skill without replacing user-authored instructions."""
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import shutil

from .dap import DebugError

MANIFEST = "installation.json"
AGENTS = {"codex": ".agents", "claude-code": ".claude"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path, base):
    # Never follow an agent-directory symlink/junction during install or removal.
    for part in (path, *path.parents):
        if part == base:
            break
        if os.path.lexists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DebugError("skill_conflict", f"Skill path contains a link or junction: {part}")


def manifest_at(target):
    file = target / MANIFEST
    if not target.exists():
        return None
    if not file.is_file() or file.stat().st_size > 65536:
        raise DebugError("skill_conflict", "Existing skill is not managed by aidebug")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        files = data["files"]
        if data["owner"] != "aidebug-cli" or not isinstance(files, dict) or not 1 <= len(files) <= 32:
            raise ValueError()
        argv = data["cli_argv"]
        if (not isinstance(data["version"], str) or not isinstance(argv, list) or len(argv) != 3 or
                not all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv) or
                argv[1:] != ["-m", "aidebug.cli"]):
            raise ValueError()
        for name, sha in files.items():
            path = PurePosixPath(name)
            if (path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")) or
                    "\\" in name or ":" in name or name == MANIFEST or
                    not isinstance(sha, str) or len(sha) != 64):
                raise ValueError()
        return data
    except (ValueError, KeyError, TypeError):
        raise DebugError("skill_conflict", "Invalid aidebug skill ownership record") from None


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def operate(target, base, action, payload, metadata):
    safe_path(target / MANIFEST, base)
    previous = manifest_at(target)
    if previous:
        for name, sha in previous["files"].items():
            file = target / name
            safe_path(file, base)
            if not file.is_file() or file.stat().st_size > 1048576 or digest(file.read_bytes()) != sha:
                raise DebugError("skill_conflict", f"Managed file changed or is missing; preserve or restore it before retrying: {name}")
    if action == "status":
        return {"status": "not_installed" if previous is None else
                "current" if previous == metadata else "outdated",
                "installed_version": previous.get("version") if previous else None,
                "recorded_python_exists": bool(previous and Path(previous.get("cli_argv", [""])[0]).is_file())}
    if action == "uninstall":
        if previous is None:
            return {"status": "not_installed"}
        for name in previous["files"]:
            (target / name).unlink()
        (target / MANIFEST).unlink()
        # Remove only empty owned directories; unrelated files remain in place.
        directories = {target}
        for name in previous["files"]:
            directories.update(p for p in (target / name).parents if p == target or target in p.parents)
        for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return {"status": "uninstalled", "unrelated_files_preserved": target.exists()}
    for name in payload:
        file = target / name
        safe_path(file, base)
        if file.exists() and (previous is None or name not in previous["files"]):
            raise DebugError("skill_conflict", f"Unmanaged file would be replaced: {name}")
    if previous == metadata:
        return {"status": "current", "version": metadata["version"]}
    for name, content in payload.items():
        atomic_write(target / name, content)
    if previous:
        for name in previous["files"].keys() - payload.keys():
            (target / name).unlink()
    atomic_write(target / MANIFEST, (json.dumps(metadata, indent=2) + "\n").encode())
    return {"status": "updated" if previous else "installed", "version": metadata["version"]}


def manage(args):
    from .cli import lock_workspace
    base = (Path.home() if args["scope"] == "user" else Path(args["workspace"])).resolve()
    source = Path(__file__).with_name("skill")
    payload = {file.relative_to(source).as_posix(): file.read_bytes() for file in source.rglob("*") if file.is_file()}
    if "SKILL.md" not in payload:
        raise DebugError("missing_dependency", "Bundled skill is missing; reinstall aidebug-cli")
    metadata = {"owner": "aidebug-cli", "version": version("aidebug-cli"),
                "cli_argv": [sys.executable, "-m", "aidebug.cli"],
                "files": {name: digest(content) for name, content in payload.items()}}
    results = []
    for agent in dict.fromkeys(args["agent"] or AGENTS):
        root = base / AGENTS[agent] / "skills"
        target = root / "aidebug"
        result = {"agent": agent, "path": str(target)}
        lock = None
        try:
            safe_path(target, base)
            if args["action"] != "status" and (root.exists() or args["action"] == "install"):
                root.mkdir(parents=True, exist_ok=True)
                safe_path(root / ".aidebug-install.lock", base)
                lock = lock_workspace(root, ".aidebug-install.lock")
                if lock is None:
                    raise DebugError("busy", "Another aidebug skill installer is using this directory")
            result.update(operate(target, base, args["action"], payload, metadata))
        except (DebugError, OSError) as error:
            result.update(status="conflict", error={"code": getattr(error, "code", "skill_io_error"), "message": str(error)[:512]})
        finally:
            if lock:
                lock.close()
        results.append(result)
    return {"ok": all("error" not in item for item in results), "action": args["action"], "scope": args["scope"],
            "cli": {"python": sys.executable, "on_path": shutil.which("aidebug"), "version": metadata["version"]},
            "skills": results,
            "discovery": "Codex: $aidebug; Claude Code: /aidebug. Start a new agent session if the skill is not listed."}
