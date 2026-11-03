#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, text=True, **kwargs)


case = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="xmake-conformance-") as temporary:
    temp = Path(temporary)
    cli = temp / "cli"
    run(["nix", "build", str(REPO) + "#cli", "--out-link", cli])
    helper = cli / "bin/xmake-purifier"
    xmake = cli / "bin/xmake"
    root = temp / "project"
    shutil.copytree(
        REPO / "examples/minimal",
        root,
        ignore=shutil.ignore_patterns(
            ".xmake",
            ".xmake-purifier",
            "build",
            "*.lock",
            "*.lock.json",
            "*.log",
            "compile_commands.json",
        ),
    )
    recipe = root / "xmake.lua"
    if case == "update":
        recipe.write_text(
            recipe.read_text().replace("zlib 1.3.1", "zlib >=1.2.13 <1.3.0")
        )
    if case == "tasks":
        recipe.write_text(
            recipe.read_text().replace(
                'target("purifier-example")',
                'add_requires("libpng 1.6.54", {system=false, configs={shared=false}})\ntarget("purifier-example")',
            )
        )
        with recipe.open("a") as stream:
            stream.write(
                '\ntask("child")\n  on_run(function () os.exec("xmake show -l packages") end)\n  set_menu {usage="xmake child", description="Child probe", options={}}\n'
            )
    if case == "tasks":
        source = root / "src/main.c"
        source.write_text(
            "#include <png.h>\n"
            + source.read_text().replace(
                'printf("locked xmake',
                'if (png_access_version_number()==0) return 2; printf("locked xmake',
            )
        )
        recipe.write_text(
            recipe.read_text().replace(
                '  add_packages("zlib")', '  add_packages("zlib", "libpng")'
            )
        )
    env = {k: v for k, v in os.environ.items() if not k.startswith("XMAKE_PURIFIER_")}
    env["XMAKE_GLOBALDIR"] = str(temp / "ordinary-global")
    env["XMAKE_PKG_INSTALLDIR"] = str(temp / "ordinary-installed")
    args = [helper, "--project", root]
    companion = root / "xmake-purifier.lock.json"
    native_lock = root / "xmake-requires.lock"
    if case == "external":
        companion = temp / "external.json"
        native_lock = temp / "external.lock"
        args += ["--lock-file", companion, "--requires-lock", native_lock]
    run([*args, "lock"], env=env)
    before = companion.read_bytes(), native_lock.read_bytes()
    run([*args, "lock"], env=env)
    assert before == (companion.read_bytes(), native_lock.read_bytes())
    if case == "update":
        first = json.loads(companion.read_text())
        assert any(
            p["version"] == "v1.2.13"
            for p in next(iter(first["profiles"].values()))["packages"]
        )
        recipe.write_text(
            recipe.read_text().replace("zlib >=1.2.13 <1.3.0", "zlib >=1.2.13")
        )
        run([*args, "lock", "--update"], env=env)
        assert any(
            p["version"] != "v1.2.13"
            for p in next(iter(json.loads(companion.read_text())["profiles"].values()))[
                "packages"
            ]
        )
    environment = Path(
        run([*args, "prefetch"], env=env, stdout=subprocess.PIPE).stdout.strip()
    )
    offline = env | {
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    }
    assert (
        Path(
            run([*args, "prefetch"], env=offline, stdout=subprocess.PIPE).stdout.strip()
        )
        == environment
    )
    if case in {"native", "external", "tasks"}:
        command = [helper, "--project", root, "--native"]
        if case == "external":
            command += ["--lock-file", companion, "--requires-lock", native_lock]
        command += ["--"]
        if case == "native":
            shutil.copytree(
                environment / "packages",
                temp / "ordinary-installed",
                dirs_exist_ok=True,
            )
            for header in (temp / "ordinary-installed").rglob("zlib.h"):
                header.chmod(0o644)
                header.write_text("ordinary cache poison is not C")
        run(command, cwd=root, env=offline)
        output = run(
            [*command, "run"], cwd=root, env=offline, stdout=subprocess.PIPE
        ).stdout
        assert "locked xmake" in output
        run([*command, "test"], cwd=root, env=offline)
        if case == "native":
            run([*command, "f", "-m", "debug"], cwd=root, env=offline)
            run(command, cwd=root, env=offline)
            assert (
                json.loads((root / ".xmake-purifier/state.json").read_text())["mode"]
                == "debug"
            )
            run([*command, "f", "-m", "release"], cwd=root, env=offline)
            run(command, cwd=root, env=offline)
            invalid = subprocess.run(
                list(map(str, [*command, "f", "-m", "unprepared"])),
                cwd=root,
                env=offline,
                text=True,
                capture_output=True,
            )
            assert invalid.returncode and "profile missing" in invalid.stderr
        source = root / "src/main.c"
        source.write_text(source.read_text().replace("locked xmake", "edited xmake"))
        run([*command, "-r"], cwd=root, env=offline)
        assert (
            "edited xmake"
            in run(
                [*command, "run"], cwd=root, env=offline, stdout=subprocess.PIPE
            ).stdout
        )
        assert (
            Path(
                run(
                    [*args, "prefetch"], env=offline, stdout=subprocess.PIPE
                ).stdout.strip()
            )
            == environment
        )
        run([*command, "project", "-k", "compile_commands"], cwd=root, env=offline)
        database = json.loads((root / "compile_commands.json").read_text())
        assert str((environment / "packages").resolve()) in json.dumps(database)
        checked = run(
            [
                cli / "bin/clangd",
                "--check=" + str(source),
                "--compile-commands-dir=" + str(root),
            ],
            cwd=root,
            env=offline,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert "0 errors" in checked.stderr, checked.stderr[-4000:]
        if case == "native":
            import editor

            editor.check(cli, root, environment, offline)
        if case == "tasks":
            run([*command, "child"], cwd=root, env=offline)
        if case == "external":
            run([*args, "shell", "--", "xmake", "show", "-l", "packages"], env=offline)
        recipe.write_text(recipe.read_text() + "\n")
        failure = subprocess.run(
            list(map(str, command)),
            cwd=root,
            env=offline,
            text=True,
            capture_output=True,
        )
        assert failure.returncode and "inputs changed" in failure.stderr
    if case == "native":
        import tarfile

        recipe.write_bytes((REPO / "examples/minimal/xmake.lua").read_bytes())
        fake = temp / "gh"
        record = temp / "calls"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\np=Path("
            + repr(str(record))
            + ')\na=sys.argv[1:]\nwith p.open("a") as f:f.write(json.dumps(a)+"\\n")\nif a[:2]==["release","view"]:sys.exit(0 if len(p.read_text().splitlines())>2 else 1)\n'
        )
        fake.chmod(0o755)
        for _ in range(2):
            run(
                [*args, "publish", "--repo", "fixture/project", "--tag", "v0.1.0"],
                env=env | {"XMAKE_PURIFIER_GH": str(fake)},
            )
        calls = [json.loads(line) for line in record.read_text().splitlines()]
        assert calls[1][:2] == ["release", "create"] and calls[3][:2] == [
            "release",
            "upload",
        ]
        with tarfile.open(
            next(v for v in calls[1] if v.endswith("/project.tar.gz"))
        ) as artifact:
            files = set(artifact.getnames())
            assert {
                "./xmake.lua",
                "./xmake-requires.lock",
                "./xmake-purifier.lock.json",
            } <= files
            assert not any(
                v.startswith("./build/")
                or v.startswith("./.xmake")
                or v == "./compile_commands.json"
                for v in files
            )
        empty_graph = temp / "empty-graph"
        (empty_graph / "src").mkdir(parents=True)
        (empty_graph / "xmake.lua").write_text(
            'set_project("empty-example")\ntarget("empty-example")\n  set_kind("binary")\n  add_files("src/main.c")\n'
        )
        (empty_graph / "src/main.c").write_text("int main(void){return 0;}")
        run([helper, "--project", empty_graph, "lock"], env=env)
        empty_environment = Path(
            run(
                [helper, "--project", empty_graph, "prefetch"],
                env=offline,
                stdout=subprocess.PIPE,
            ).stdout.strip()
        )
        assert list((empty_environment / "packages").iterdir()) == []
    if case == "native":
        empty = temp / "unlocked"
        empty.mkdir()
        run(["nix", "build", str(REPO) + "#native", "--out-link", temp / "native"])
        for tool in ["xmake", "xrepo"]:
            raw = temp / "native/bin" / tool
            wrapper = cli / "bin" / tool
            baseline = run([raw, "--version"], cwd=empty, env=env, capture_output=True)
            fallback = run(
                [wrapper, "--version"], cwd=empty, env=env, capture_output=True
            )
            assert (
                baseline.stdout == fallback.stdout
                and "ordinary impure " + tool in fallback.stderr
            )
    if case == "native":
        (empty / "xmake.lua").write_text(
            'task("fallback")\n  on_run(function () os.execv("sh", {"-c", "cat; printf native-stderr >&2; exit 7"}) end)\n  set_menu {usage="xmake fallback", description="Fallback probe", options={}}\n'
        )
        baseline = subprocess.run(
            [str(temp / "native/bin/xmake"), "fallback"],
            cwd=empty,
            env=env,
            input="native-stdin\n",
            text=True,
            capture_output=True,
        )
        fallback = subprocess.run(
            [str(xmake), "fallback"],
            cwd=empty,
            env=env,
            input="native-stdin\n",
            text=True,
            capture_output=True,
        )
        assert (
            baseline.returncode == fallback.returncode and baseline.returncode != 0
        ), (baseline, fallback)
        assert all(
            "native-stdin" in p.stdout and "native-stderr" in p.stderr
            for p in [baseline, fallback]
        ), (baseline, fallback)
        assert "ordinary impure xmake" in fallback.stderr
    print("Xmake conformance passed:", case)
