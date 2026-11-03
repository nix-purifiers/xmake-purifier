# SPDX-License-Identifier: MIT
"""Capture native Xmake selections and activate immutable Nix packages."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

PREFIX = "XMAKE_PURIFIER_"
SAFE = re.compile(r"[A-Za-z0-9_-]+\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
CONSTANTS = {
    "NATIVE",
    "XREPO",
    "SYSTEM",
    "XMAKE_VERSION",
    "GCC_VERSION",
    "SDK_VERSION",
    "WRAPPER_BIN",
    "PROGRAM_DIR",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(data):
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def atomic(path, data):
    data = data.encode() if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    fd, filename = tempfile.mkstemp(prefix=".xmake-purifier-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def native(tool="xmake"):
    return os.environ[PREFIX + ("NATIVE" if tool == "xmake" else "XREPO")]


def versions():
    return {
        name: os.environ[PREFIX + name.upper() + "_VERSION"]
        for name in ("xmake", "gcc", "sdk")
    }


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(PREFIX)}
    if os.environ.get(PREFIX + "BASE_XMAKE_ENV"):
        for name, value in json.loads(os.environ[PREFIX + "BASE_XMAKE_ENV"]).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
    return env


def valid_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "source URLs must be public HTTPS without credentials, queries or fragments"
        )
    return url


def inputs_match(root, inputs):
    return (
        bool(inputs)
        and "xmake.lua" in inputs
        and all(
            (root / name).is_file()
            and digest((root / name).read_bytes()) == value["sha256"]
            for name, value in inputs.items()
        )
    )


def capture_git(repo, scratch):
    url = valid_url(repo["url"])
    rev = repo["commit"]
    if not re.fullmatch("[0-9a-f]{40}", rev):
        raise ValueError("native repository lacks an exact Git commit")
    checkout = Path(repo["directory"])
    actual = run(
        ["git", "-C", checkout, "rev-parse", "HEAD"], stdout=subprocess.PIPE, text=True
    ).stdout.strip()
    if actual != rev:
        raise ValueError("native recipe repository moved during capture")
    target = scratch / ("repo-" + rev)
    target.mkdir(exist_ok=True)
    records = run(
        ["git", "-C", checkout, "ls-tree", "-r", "-z", "HEAD"], stdout=subprocess.PIPE
    ).stdout.split(b"\0")
    for record in records:
        if not record:
            continue
        header, filename = record.split(b"\t", 1)
        if header.split()[0] == b"160000":
            raise ValueError("recipe repository submodules are not yet supported")
        relative = Path(os.fsdecode(filename))
        source, destination = checkout / relative, target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            if not destination.is_symlink():
                destination.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, destination)
    nar_hash = run(
        ["nix", "hash", "path", "--sri", target], stdout=subprocess.PIPE, text=True
    ).stdout.strip()
    return {
        "path": "repositories/" + repo["name"] + "/" + rev,
        "hash": nar_hash,
        "git": {
            "url": url,
            "rev": rev,
            "submodules": False,
            "leaveDotGit": False,
            "commitObject": None,
        },
    }


def capture_profile(project, scratch, env, mode, lock_text, upgrade):
    config = scratch / ("config-" + mode)
    capture = scratch / ("capture-" + mode + ".json")
    env = env | {
        "XMAKE_CONFIGDIR": str(config),
        PREFIX + "CAPTURE": str(capture),
        PREFIX + "PROJECT": str(project),
    }
    native_lock = project / "xmake-requires.lock"
    if lock_text:
        native_lock.write_text(lock_text)
    else:
        native_lock.unlink(missing_ok=True)
    run([native(), "f", "-c", "-m", mode, "--require=n", "-y"], cwd=project, env=env)
    args = [
        native(),
        "require",
        "--download",
        "--build",
        "-y",
        "--packagedir=" + str(scratch / "downloads"),
    ]
    if upgrade:
        args.append("--upgrade")
    run(args, cwd=project, env=env)
    data = json.loads(capture.read_text())
    if (
        data["mode"] != mode
        or data["nativeLock"].get("__meta__", {}).get("version") != "1.0"
    ):
        raise ValueError("unsupported native Xmake lock/profile format")
    manifests = {}
    for filename in data["files"]:
        source = Path(filename)
        try:
            relative = source.relative_to(project)
        except ValueError:
            if str(source).startswith("/nix/store/"):
                continue
            raise ValueError(
                "project Lua inputs must be inside the project: " + str(source)
            )
        text = source.read_bytes().decode()
        manifests[str(relative)] = {
            "content": text,
            "sha256": digest(source.read_bytes()),
        }
    if "xmake.lua" not in manifests:
        raise ValueError("native capture did not report xmake.lua")
    entries, repositories, packages = {}, {}, []
    archive_records = [
        json.loads(p.read_text())
        for p in Path(str(capture) + ".archives").glob("*.json")
    ]
    for package in data["packages"]:
        name = package["name"]
        if (
            not SAFE.fullmatch(name)
            or package.get("system")
            or package.get("thirdparty")
            or not package.get("repo")
        ):
            raise ValueError(
                "initial support requires source packages from pinned Xmake recipe repositories"
            )
        if package["kind"] not in {"library", "binary"}:
            raise ValueError("package kind is not yet supported: " + package["kind"])
        if package.get("resources"):
            raise ValueError(
                "additional package resources need an explicit fetch adapter: " + name
            )
        for patch in package.get("patches") or []:
            if isinstance(patch, dict):
                values = patch.values()
            elif isinstance(patch, list):
                values = patch
            else:
                values = [patch]
            if any(isinstance(v, str) and "://" in v for v in values):
                raise ValueError("remote patches are not yet captured: " + name)
        repo = package["repo"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo["name"]) or repo["name"] in {
            ".",
            "..",
        }:
            raise ValueError("invalid native repository name")
        if repo["name"] not in repositories:
            entry = capture_git(repo, scratch)
            entries[entry["path"]] = entry
            repositories[repo["name"]] = {
                k: repo[k] for k in ("name", "url", "branch", "commit") if k in repo
            }
        elif repositories[repo["name"]]["commit"] != repo["commit"]:
            raise ValueError(
                "multiple revisions of one named repository need separate profiles"
            )
        archives = [
            a
            for a in archive_records
            if a["name"] == name and a["version"] == package["version"]
        ]
        if not archives:
            raise ValueError(
                "native source archive was not captured for "
                + name
                + "; Git-only packages are not yet supported"
            )
        for archive in archives:
            raw_hash = archive["sha256"]
            native_hashes = [
                u.get("sourcehash")
                for scheme in package["urls"]
                for u in scheme["urls"]
            ]
            if raw_hash not in native_hashes:
                raise ValueError("native archive checksum is required for " + name)
            if not HEX.fullmatch(raw_hash):
                raise ValueError("invalid native archive checksum")
            payload = Path(str(capture) + ".archives") / raw_hash
            if digest(payload.read_bytes()) != raw_hash:
                raise ValueError("captured source archive changed")
            entries[raw_hash] = {
                "path": raw_hash,
                "url": valid_url(archive["url"]),
                "hash": "sha256-" + base64.b64encode(bytes.fromhex(raw_hash)).decode(),
                "mirrors": [],
            }
        packages.append(
            {
                "name": name,
                "version": package["version"],
                "buildhash": package["buildhash"],
                "kind": package["kind"],
                "require": package["require"],
                "requires": package["requires"],
                "repo": repositories[repo["name"]],
            }
        )
    return {
        "system": os.environ[PREFIX + "SYSTEM"],
        "mode": mode,
        "nativeLock": native_lock.read_text(),
        "entries": sorted(entries.values(), key=lambda e: e["path"]),
        "repositories": sorted(repositories.values(), key=lambda r: r["name"]),
        "packages": sorted(
            packages,
            key=lambda p: (p["name"], p["version"], p["buildhash"], p["require"]),
        ),
        "manifests": manifests,
    }


def lock_project(root, companion, requires_lock, modes, upgrade):
    if not (root / "xmake.lua").is_file():
        raise ValueError("xmake.lua is required")
    previous = json.loads(companion.read_text()) if companion.exists() else {}
    preserve = previous.get("toolVersions") == versions() and inputs_match(
        root, previous.get("manifests", {})
    )
    profiles = previous.get("profiles", {}).copy() if preserve else {}
    inputs = previous.get("manifests", {}).copy() if preserve else {}
    modes = modes or ["release", "debug"]
    with tempfile.TemporaryDirectory(prefix="xmake-lock-") as temporary:
        scratch = Path(temporary)
        project = scratch / "project"
        shutil.copytree(
            root,
            project,
            ignore=shutil.ignore_patterns(
                ".git",
                ".xmake",
                ".xmake-purifier",
                "build",
                "result",
                "__pycache__",
                "*.log",
            ),
        )
        for folder in ["home", "global", "cache", "installed"]:
            (scratch / folder).mkdir()
        env = clean_env() | {
            "HOME": str(scratch / "home"),
            "XMAKE_GLOBALDIR": str(scratch / "global"),
            "XMAKE_PKG_CACHEDIR": str(scratch / "cache"),
            "XMAKE_PKG_INSTALLDIR": str(scratch / "installed"),
            "XMAKE_PROGRAM_DIR": os.environ[PREFIX + "PROGRAM_DIR"],
            "XMAKE_PROJECT_DIR": str(project),
        }
        names = []
        if preserve:
            for old_profile in profiles.values():
                for repo in old_profile["repositories"]:
                    if repo not in names:
                        names.append(repo)
        name_file = scratch / "repository-names.json"
        atomic(name_file, dump(names))
        env[PREFIX + "REPO_NAMES"] = str(name_file)
        last = None
        for mode in modes:
            if not SAFE.fullmatch(mode):
                raise ValueError("invalid mode")
            profile_key = os.environ[PREFIX + "SYSTEM"] + ":" + mode
            old = profiles.get(profile_key, {}).get("nativeLock")
            lock_text = old or (
                requires_lock.read_text() if requires_lock.exists() else None
            )
            profile = capture_profile(project, scratch, env, mode, lock_text, upgrade)
            profiles[profile_key] = profile
            for repo in profile["repositories"]:
                if repo not in names:
                    names.append(repo)
            atomic(name_file, dump(names))
            inputs.update(profile["manifests"])
            last = profile["nativeLock"]
        data = {
            "version": 1,
            "toolVersions": versions(),
            "nativeHash": digest(last.encode()),
            "manifests": inputs,
            "profiles": profiles,
        }
        atomic(requires_lock, last)
        atomic(companion, dump(data))


def validate(root, companion, requires_lock):
    data = json.loads(companion.read_text())
    if data.get("version") != 1 or not inputs_match(root, data.get("manifests", {})):
        raise ValueError("Xmake project inputs changed; run xmake-purifier lock")
    if data.get("toolVersions") != versions():
        raise ValueError("Xmake SDK changed; run xmake-purifier lock")
    if not requires_lock.exists() or digest(requires_lock.read_bytes()) != data.get(
        "nativeHash"
    ):
        raise ValueError("native Xmake lock changed; run xmake-purifier lock")
    return data


def realize(root, companion, requires_lock, mode, target="environment"):
    data = validate(root, companion, requires_lock)
    if os.environ[PREFIX + "SYSTEM"] + ":" + mode not in data["profiles"]:
        raise ValueError(
            "mode/system profile missing; run xmake-purifier lock --mode " + mode
        )
    expression = Path(__file__).resolve().parent.parent / "nix/realize.nix"
    command = [
        "nix",
        "build",
        "--impure",
        "--file",
        str(expression),
        "--no-link",
        "--print-out-paths",
    ]
    for name, value in {
        "project": root,
        "nativeLock": requires_lock,
        "companion": companion,
        "mode": mode,
        "target": target,
    }.items():
        command += ["--argstr", name, str(value)]
    return Path(
        run(command, text=True, stdout=subprocess.PIPE).stdout.strip().splitlines()[-1]
    )


def session(root, environment, companion, requires_lock):
    folder = root / ".xmake-purifier/sessions" / environment.name
    folder.mkdir(parents=True, exist_ok=True)
    data = json.loads((environment / "metadata.json").read_text())
    for name, target in {
        "environment": environment,
        "sdk": environment / "sdk",
    }.items():
        if not (folder / name).is_symlink():
            (folder / name).symlink_to(target)
    atomic(folder / "xmake-requires.lock", data["profile"]["nativeLock"])
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(PREFIX) or k in {PREFIX + n for n in CONSTANTS}
    }
    base_names = [
        "XMAKE_GLOBALDIR",
        "XMAKE_CONFIGDIR",
        "XMAKE_PKG_CACHEDIR",
        "XMAKE_PKG_INSTALLDIR",
        "XMAKE_PROGRAM_DIR",
        "XMAKE_PROJECT_DIR",
    ]
    env[PREFIX + "BASE_XMAKE_ENV"] = os.environ.get(
        PREFIX + "BASE_XMAKE_ENV",
        json.dumps({name: os.environ.get(name) for name in base_names}),
    )
    env |= {
        "XMAKE_PROGRAM_DIR": os.environ[PREFIX + "PROGRAM_DIR"],
        "XMAKE_PROJECT_DIR": str(root),
        "XMAKE_GLOBALDIR": str(folder / "global"),
        "XMAKE_CONFIGDIR": str(folder / "config"),
        "XMAKE_PKG_CACHEDIR": str(folder / "cache"),
        "XMAKE_PKG_INSTALLDIR": str((environment / "packages").resolve()),
        PREFIX + "REPOSITORIES": str((environment / "repositories").resolve()),
        PREFIX + "REPOSITORIES_FILE": str(environment / "repositories.json"),
        PREFIX + "PROFILE": str(environment / "profile.json"),
        PREFIX + "FROZEN": "1",
        PREFIX + "NATIVE_LOCK": str(folder / "xmake-requires.lock"),
        PREFIX + "PROJECT": str(root),
        PREFIX + "ENVIRONMENT": str(environment),
        PREFIX + "PUBLIC_LOCKFILE": str(companion),
        PREFIX + "PUBLIC_NATIVE_LOCK": str(requires_lock),
    }
    run(
        [
            "nix-store",
            "--add-root",
            folder / "gcroot",
            "--indirect",
            "--realise",
            environment,
        ],
        stdout=subprocess.DEVNULL,
    )
    return env


def selection(root, args):
    state = root / ".xmake-purifier/state.json"
    mode = (
        json.loads(state.read_text()).get("mode", "release")
        if state.exists()
        else "release"
    )
    for i, arg in enumerate(args):
        if arg == "--":
            break
        if arg in ("-m", "--mode"):
            mode = args[i + 1]
        elif arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif arg.startswith("-m") and len(arg) > 2:
            mode = arg[2:]
    if not SAFE.fullmatch(mode):
        raise ValueError("invalid mode")
    forbidden = (
        "--project",
        "--file",
        "--require",
        "--toolchain",
        "--cc",
        "--cxx",
        "--ld",
        "--ar",
        "--arch",
        "--plat",
        "--policies",
        "--sdk",
        "--global",
        "--debugdir",
    )
    for arg in args:
        if arg == "--":
            break
        if (
            any(arg == x or arg.startswith(x + "=") for x in forbidden)
            or arg in ("-P", "-F")
            or (
                args[:1] in (["f"], ["config"])
                and (arg.startswith("-a") or arg.startswith("-p"))
            )
        ):
            raise ValueError("unsupported locked Xmake override: " + arg)
    action = next((a for a in args if not a.startswith("-")), "")
    if action in ("require", "repo", "global", "g", "update", "lua", "l"):
        raise ValueError("use xmake-purifier lock for dependency changes")
    return mode, state


def exit_status(code):
    if code < 0:
        os.kill(os.getpid(), -code)
    sys.exit(code)


def main():
    try:
        parser = argparse.ArgumentParser(add_help="--native" not in sys.argv)
        parser.add_argument("--project", default=".")
        parser.add_argument("--lock-file")
        parser.add_argument("--requires-lock")
        parser.add_argument("--native", action="store_true")
        parser.add_argument("--tool", choices=["xmake", "xrepo"], default="xmake")
        options, args = parser.parse_known_args()
        if args[:1] == ["--"]:
            args = args[1:]
        root = Path(options.project).resolve()
        companion = (
            Path(options.lock_file).resolve()
            if options.lock_file
            else root / "xmake-purifier.lock.json"
        )
        requires_lock = (
            Path(options.requires_lock).resolve()
            if options.requires_lock
            else root / "xmake-requires.lock"
        )
        if os.environ.get(PREFIX + "PROJECT") == str(root):
            if not options.lock_file:
                companion = Path(os.environ[PREFIX + "PUBLIC_LOCKFILE"])
            if not options.requires_lock:
                requires_lock = Path(os.environ[PREFIX + "PUBLIC_NATIVE_LOCK"])
        if options.native:
            program = native(options.tool)
            if not companion.exists():
                print(
                    "xmake-purifier: no lockfile; using ordinary impure "
                    + options.tool
                    + " resolution",
                    file=sys.stderr,
                )
                os.execvpe(program, [program] + args, clean_env())
            if args in (["--version"], ["--help"], ["-h"]):
                validate(root, companion, requires_lock)
                os.execvpe(program, [program] + args, clean_env())
            if options.tool == "xrepo" and (
                not args or args[0] not in {"info", "list", "scan", "search"}
            ):
                raise ValueError(
                    "use xmake-purifier lock to manage locked project packages"
                )
            mode, state = selection(root, args)
            environment = realize(root, companion, requires_lock, mode)
            env = session(root, environment, companion, requires_lock)
            marker = root / ".xmake-purifier/sessions" / environment.name / "configured"
            os.chdir(root)
            if args[:1] in (["f"], ["config"]):
                result = subprocess.run([program] + args, env=env)
                if result.returncode == 0:
                    atomic(state, dump({"mode": mode}))
                    atomic(marker, mode)
                exit_status(result.returncode)
            if not marker.exists():
                result = subprocess.run(
                    [native(), "f", "-m", mode, "--require=n", "-y"],
                    cwd=root,
                    env=env,
                    text=True,
                    capture_output=True,
                )
                if result.returncode:
                    sys.stdout.write(result.stdout)
                    sys.stderr.write(result.stderr)
                    exit_status(result.returncode)
                atomic(marker, mode)
            os.execvpe(program, [program] + args, env)
        action = args.pop(0) if args else "help"
        if action == "lock":
            p = argparse.ArgumentParser()
            p.add_argument("--mode", action="append")
            p.add_argument("--update", action="store_true")
            opts = p.parse_args(args)
            lock_project(root, companion, requires_lock, opts.mode, opts.update)
        elif action in {"prefetch", "shell", "release"}:
            p = argparse.ArgumentParser()
            p.add_argument("--mode", default="release")
            opts, command = p.parse_known_args(args)
            environment = realize(
                root,
                companion,
                requires_lock,
                opts.mode,
                "release" if action == "release" else "environment",
            )
            if action in {"prefetch", "release"}:
                print(environment)
            else:
                if command[:1] == ["--"]:
                    command = command[1:]
                atomic(root / ".xmake-purifier/state.json", dump({"mode": opts.mode}))
                env = session(root, environment, companion, requires_lock)
                env["PATH"] = (
                    os.environ[PREFIX + "WRAPPER_BIN"] + os.pathsep + env["PATH"]
                )
                os.chdir(root)
                os.execvpe(
                    command[0] if command else os.environ.get("SHELL", "sh"),
                    command or [os.environ.get("SHELL", "sh")],
                    env,
                )
        elif action == "publish":
            p = argparse.ArgumentParser()
            p.add_argument("--repo", required=True)
            p.add_argument("--tag", required=True)
            opts = p.parse_args(args)
            artifact = (
                realize(root, companion, requires_lock, "release", "release")
                / "project.tar.gz"
            )
            gh = os.environ.get(PREFIX + "GH", "gh")
            exists = (
                subprocess.run(
                    [gh, "release", "view", opts.tag, "--repo", opts.repo],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
            run(
                [
                    gh,
                    "release",
                    "upload",
                    opts.tag,
                    artifact,
                    "--repo",
                    opts.repo,
                    "--clobber",
                ]
                if exists
                else [
                    gh,
                    "release",
                    "create",
                    opts.tag,
                    artifact,
                    "--repo",
                    opts.repo,
                    "--title",
                    opts.tag,
                    "--notes",
                    "Locked Xmake project source release",
                ]
            )
        else:
            parser.error("expected lock, prefetch, shell, release or publish")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print("xmake-purifier: " + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
