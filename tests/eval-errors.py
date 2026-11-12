#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="xmake-eval-") as temporary:
    root = Path(temporary) / "project"
    shutil.copytree(
        REPO / "examples/minimal",
        root,
        ignore=shutil.ignore_patterns(
            ".xmake", ".xmake-purifier", "build", "*.log", "compile_commands.json"
        ),
    )
    path = root / "xmake-purifier.lock.json"
    original = json.loads(path.read_text())
    profile = next(iter(original["profiles"]))

    def evaluate():
        expr = f"let f=builtins.getFlake {json.dumps(str(REPO))}; in (import {REPO}/lib-overlays/default/read-lock.nix {{ lib=f.inputs.nixpkgs.lib; }} {{ src={root}; }}).problems"
        return json.loads(
            subprocess.check_output(
                ["nix", "eval", "--impure", "--json", "--expr", expr], text=True
            )
        )

    assert evaluate() == []
    mutations = [
        lambda d: d.update(version=99),
        lambda d: d.update(profiles={}),
        lambda d: d.update(nativeHash="bad"),
        lambda d: d["profiles"][profile].update(mode="../escape"),
        lambda d: d["profiles"][profile].update(system="wrong"),
        lambda d: d["profiles"][profile]["packages"][0].update(buildhash="bad"),
        lambda d: d["profiles"][profile]["entries"][0].update(path="../escape"),
        lambda d: d["profiles"][profile]["entries"][0].update(hash="bad"),
        lambda d: d["profiles"][profile]["manifests"].update(
            {"../escape": {"content": "bad", "sha256": "bad"}}
        ),
        lambda d: d["profiles"][profile]["repositories"][0].update(commit="branch"),
    ]
    for mutation in mutations:
        data = copy.deepcopy(original)
        mutation(data)
        path.write_text(json.dumps(data))
        assert evaluate()
    path.write_text(json.dumps(original))
    (root / "xmake.lua").write_text('set_project("changed")')
    assert evaluate()
print("Xmake malformed/stale profiles rejected before fetching")
