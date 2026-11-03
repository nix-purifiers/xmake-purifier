# SPDX-License-Identifier: MIT
"""Verify clangd navigates an include into the immutable Xmake package."""

import json, os, select, signal, subprocess, time, tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check(cli, root, environment, env):
    source = root / "src/main.c"
    settings = {}
    server_env = {**env, "PATH": str(cli / "bin") + os.pathsep + env.get("PATH", "")}
    with (
        tempfile.TemporaryDirectory(prefix="xmake-editor-") as scratch,
        (Path(scratch) / "editor.log").open("w") as log,
    ):
        process = subprocess.Popen(
            [
                str(cli / "bin/clangd"),
                "--background-index=false",
                "--compile-commands-dir=" + str(root),
            ],
            cwd=root,
            env=server_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        buffer = b""

        def send(value):
            body = json.dumps(value).encode()
            process.stdin.write(
                ("Content-Length: " + str(len(body)) + "\r\n\r\n").encode() + body
            )
            process.stdin.flush()

        def receive(deadline):
            nonlocal buffer
            while True:
                head, separator, body = buffer.partition(b"\r\n\r\n")
                if separator:
                    headers = dict(
                        line.decode().split(":", 1) for line in head.split(b"\r\n")
                    )
                    length = int(headers["Content-Length"])
                    if len(body) >= length:
                        buffer = body[length:]
                        return json.loads(body[:length])
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                    raise TimeoutError("clangd response timed out")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(
                        "clangd exited: " + Path(log.name).read_text()[-4000:]
                    )
                buffer += chunk

        def wait(identifier, deadline):
            while True:
                message = receive(deadline)
                if "method" in message and "id" in message:
                    result = (
                        [
                            settings if item.get("section") == "d" else {}
                            for item in message.get("params", {}).get("items", [])
                        ]
                        if message["method"] == "workspace/configuration"
                        else None
                    )
                    send({"jsonrpc": "2.0", "id": message["id"], "result": result})
                elif message.get("id") == identifier:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result")

        try:
            deadline = time.monotonic() + 45
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "processId": os.getpid(),
                        "rootUri": root.as_uri(),
                        "capabilities": {"workspace": {"configuration": True}},
                        "workspaceFolders": [{"uri": root.as_uri(), "name": "project"}],
                    },
                }
            )
            initialized = wait(1, deadline)
            assert initialized["capabilities"]["definitionProvider"], initialized
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": source.as_uri(),
                            "languageId": "c",
                            "version": 1,
                            "text": source.read_text(),
                        }
                    },
                }
            )
            for attempt in range(2, 82):
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": attempt,
                        "method": "textDocument/definition",
                        "params": {
                            "textDocument": {"uri": source.as_uri()},
                            "position": {"line": 1, "character": 11},
                        },
                    }
                )
                result = wait(attempt, deadline)
                if result:
                    break
                time.sleep(0.5)
            locations = result if isinstance(result, list) else [result]
            paths = [
                Path(
                    unquote(urlsplit(item.get("uri", item.get("targetUri", ""))).path)
                ).resolve()
                for item in locations
                if item
            ]
            assert paths and any(
                path.is_relative_to((environment / "packages").resolve())
                and path.name == "zlib.h"
                for path in paths
            ), result
            send({"jsonrpc": "2.0", "id": 100, "method": "shutdown", "params": None})
            wait(100, time.monotonic() + 5)
            send({"jsonrpc": "2.0", "method": "exit", "params": None})
            process.stdin.close()
            status = process.wait(timeout=5)
            assert status == 0, (status, Path(log.name).read_text()[-4000:])
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
