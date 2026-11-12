# SPDX-License-Identifier: MIT
{
  lib,
  runCommand,
  fetchgit,
  python3,
  writeText,
  linkFarm,
  nativeXmake,
  toolchain,
  gcc,
  stdenv,
  purifier,
  gnutar,
  gzip,
}:
{
  src,
  nativeLibraries ? { },
  nativeIncludeDirectories ? [ ],
  lockFile ? src + "/xmake-purifier.lock.json",
  requiresLock ? src + "/xmake-requires.lock",
  mode ? "release",
  fetchers ? { },
  nativeBuildInputs ? [ ],
  doCheck ? true,
  buildCommand ? "xmake -y",
  installCommand ? ''xmake install -y -o "$out"'',
  srcFilter ?
    path: type:
    !(builtins.elem (baseNameOf path) [
      ".git"
      ".xmake"
      ".xmake-purifier"
      "build"
      "result"
      "__pycache__"
      "compile_commands.json"
    ])
    && !(lib.hasSuffix ".log" (baseNameOf path)),
}:
let
  lock = purifier.checkLock (
    import ../../lib-overlays/default/read-lock.nix { inherit lib; } {
      inherit src lockFile requiresLock;
    }
  );
  environment =
    import ./environment.nix
      {
        inherit
          lib
          runCommand
          fetchgit
          python3
          writeText
          linkFarm
          nativeXmake
          toolchain
          gcc
          stdenv
          purifier
          ;
      }
      {
        inherit
          lock
          mode
          fetchers
          nativeBuildInputs
          ;
      };
  source = lib.cleanSourceWith {
    inherit src;
    filter = srcFilter;
  };
  builtProject =
    runCommand "purified-xmake-project"
      {
        nativeBuildInputs = [
          toolchain
          python3
        ]
        ++ nativeBuildInputs;
      }
      ''
        export HOME="$TMPDIR/home" XMAKE_GLOBALDIR="$TMPDIR/global" XMAKE_CONFIGDIR="$TMPDIR/config"
        export XMAKE_PKG_CACHEDIR="$TMPDIR/cache" XMAKE_PKG_INSTALLDIR=$(readlink -f ${environment}/packages)
        export XMAKE_PURIFIER_REPOSITORIES=$(readlink -f ${environment}/repositories)
        export XMAKE_PURIFIER_REPOSITORIES_FILE=${environment}/repositories.json
        export XMAKE_PURIFIER_PROFILE=${environment}/profile.json XMAKE_PURIFIER_FROZEN=1
        export SOURCE_DATE_EPOCH=1
        mkdir -p "$HOME" project
        cp -r ${source}/. project/
        chmod -R u+w project
        cd project
        export XMAKE_PURIFIER_PROJECT="$PWD" XMAKE_PURIFIER_NATIVE_LOCK="$TMPDIR/profile.lock"
        python3 -c 'import json,sys; sys.stdout.write(json.load(open(sys.argv[1]))["nativeLock"])' ${environment}/profile.json > "$XMAKE_PURIFIER_NATIVE_LOCK"
        xmake f -m ${lib.escapeShellArg mode} --require=n -y
        ${buildCommand}
        ${lib.optionalString doCheck "xmake test -y"}
        ${installCommand}
      '';
  projections = lib.optionalAttrs (nativeLibraries != { }) (
    purifier.nativeProjections {
      producer = builtProject;
      libraries = nativeLibraries;
      includeDirectories = nativeIncludeDirectories;
      system = stdenv.hostPlatform.system;
      targetMetadata.xmake = nativeXmake.version;
      inherit runCommand python3;
    }
  );
  project = builtProject // {
    inherit projections;
  };
  release =
    runCommand "xmake-project-release"
      {
        nativeBuildInputs = [
          gnutar
          gzip
        ];
      }
      ''
        mkdir source "$out"
        cp -r ${source}/. source/
        chmod -R u+w source
        cp ${lockFile} source/xmake-purifier.lock.json
        cp ${requiresLock} source/xmake-requires.lock
        tar --sort=name --mtime=@1 --owner=0 --group=0 --numeric-owner -C source -cf - . | gzip -n > "$out/project.tar.gz"
      '';
in
assert lock.problems == [ ];
{
  packages = {
    inherit environment project;
    default = project;
  };
  tests = project;
  inherit release projections;
  fetches = lock.entries;
}
