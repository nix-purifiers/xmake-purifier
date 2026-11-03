# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import sys

metadata = json.loads(Path(sys.argv[1]).read_text())
root = Path(sys.argv[2])
for name, value in metadata["profile"]["manifests"].items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value["content"].encode())
(root / "xmake-requires.lock").write_bytes(metadata["profile"]["nativeLock"].encode())
# Dependency preparation needs only the native build definitions. The application
# is compiled from the working tree, using the resulting immutable packages.
(root / "src").mkdir(exist_ok=True)
(root / "src/main.c").write_text("int main(void) { return 0; }\n")
