# xmake-purifier

Locked, Nix-resolved dependencies for Xmake. Build and test C/C++ projects in
your working tree with native Xmake and prepared libraries.

## Quickstart

From a project with `xmake.lua`:

```sh
nix shell 'github:nix-purifiers/xmake-purifier'
xmake-purifier lock
xmake-purifier prefetch
xmake
xmake test
```

The shell provides the Xmake wrapper, GCC, common native build tools and clangd.
A wrapper is required to activate the locked packages. Without a companion
lockfile it warns and runs ordinary, impure Xmake. Install it with your usual
installation method if you want it available beyond the shell.

Commit `xmake-requires.lock` and `xmake-purifier.lock.json`. Native Xmake selects
the package versions and recipe repository commits. The companion records
source archive hashes, repository trees, loaded Lua inputs and native build IDs.
Locking downloads sources; dependency compilation happens in Nix.

## Configuration and updates

Release and debug profiles are prepared by default:

```sh
xmake f -m debug
xmake
xmake f -m release
xmake
```

Application source edits reuse the immutable dependency environment. Changes to
loaded project Lua files, native selections or the SDK require locking again.
Each profile uses private native configuration and cache directories under
`.xmake-purifier/`; ordinary installed packages and recipe overrides cannot
replace the prepared libraries.

```sh
xmake-purifier lock --update
xmake-purifier prefetch
```

Updates follow native Xmake's lock semantics. For another mode, use
`xmake-purifier lock --mode MODE`, then `prefetch --mode MODE`.
Profiles must be captured natively on each supported system:
`x86_64-linux` and `aarch64-linux`.

## Editors

Generate the normal compilation database, then start clangd from the same shell:

```sh
xmake project -k compile_commands
clangd
```

The compilation database contains the Nix library include paths. The integration
retains the environment and SDK with GC roots in the project session.
A project shell is also available with `xmake-purifier shell`.

Ignore `.xmake/`, `.xmake-purifier/`, `build/` and `compile_commands.json` in Git.

## Build the project in Nix

Optionally use the overlay's `pkgs.xmake-purifier.buildXmakeProject`:

```nix
pkgs.xmake-purifier.buildXmakeProject {
  src = ./.;
}
```

It returns `packages.environment`, `packages.project`, `packages.default`,
`tests`, `release` and `fetches`; output names are known during evaluation.
`mode`, `fetchers`, `nativeBuildInputs`, `doCheck`, `buildCommand`,
`installCommand` and `srcFilter` customize the build. The default runs native
build/test/install commands. `release` is a source tarball with both lockfiles.
`xmake-purifier publish --repo OWNER/REPO --tag vVERSION` uploads it to GitHub.

## Current scope

Xmake 3.1.2 and GCC on Linux. Source libraries and binary tools from pinned
Git recipe repositories are supported. Archives need native checksums.
Local recipe patches are included in the repository snapshot. Git-only package
sources, extra resource downloads, remote patches, third-party/system providers
and package-managed toolchains need further adapters.

Project Lua inputs must be in the project; loaded SDK scripts remain part of the
pinned toolchain. Additional non-Lua files that dynamically change dependency
selection need explicit integration. The native graph must match the captured
versions, recipes and build IDs during preparation. An unprepared package fails
instead of being installed outside Nix. Compiler, architecture, recipe and
package-management overrides are guarded in locked workflows.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
