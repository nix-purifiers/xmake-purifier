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
}:
{
  lock,
  mode ? "release",
  fetchers ? { },
  nativeBuildInputs ? [ ],
}:
let
  checked = purifier.checkLock lock;
  profile =
    checked.data.profiles."${stdenv.hostPlatform.system}:${mode}"
      or (throw "xmake-purifier: native mode/system profile missing; run xmake-purifier lock");
  sources = purifier.tree (
    {
      name = "locked-xmake-sources";
      entries = profile.entries;
      inherit
        runCommand
        fetchgit
        python3
        writeText
        ;
    }
    // fetchers
  );
  repositories = linkFarm "xmake-pinned-repositories" (
    map (r: {
      name = r.name;
      path = "${sources}/repositories/${r.name}/${r.commit}";
    }) profile.repositories
  );
  repoMetadata = writeText "xmake-repositories.json" (builtins.toJSON profile.repositories);
  profileMetadata = writeText "xmake-profile.json" (builtins.toJSON profile);
  metadata = writeText "xmake-build-metadata.json" (
    builtins.toJSON {
      inherit profile;
      inherit (checked.data) manifests;
    }
  );
in
assert lib.assertMsg (
  checked.data.toolVersions.xmake == nativeXmake.version
  && checked.data.toolVersions.gcc == gcc.version
  && checked.data.toolVersions.sdk == builtins.hashFile "sha256" ./xmake.patch
) "xmake-purifier: native SDK changed; run xmake-purifier lock";
runCommand "purified-xmake-environment"
  {
    nativeBuildInputs = [
      toolchain
      python3
    ]
    ++ nativeBuildInputs;
  }
  ''
    mkdir -p "$out/packages" "$TMPDIR/home" work
    export HOME="$TMPDIR/home" XMAKE_GLOBALDIR="$TMPDIR/global" XMAKE_CONFIGDIR="$TMPDIR/config"
    export XMAKE_PKG_CACHEDIR="$TMPDIR/cache" XMAKE_PKG_INSTALLDIR="$out/packages"
    export XMAKE_PURIFIER_REPOSITORIES=${repositories} XMAKE_PURIFIER_REPOSITORIES_FILE=${repoMetadata}
    export XMAKE_PURIFIER_BLOBS=${sources} XMAKE_PURIFIER_PROFILE=${profileMetadata}
    export SOURCE_DATE_EPOCH=1
    python3 ${../xmake_purifier/build.py} ${metadata} "$PWD/work"
    cd work
    xmake g --network=private
    xmake f -m ${lib.escapeShellArg mode} --require=n -y
    xmake require --build -y
    ln -s ${toolchain} "$out/sdk"
    ln -s ${repositories} "$out/repositories"
    ln -s ${sources} "$out/sources"
    cp ${repoMetadata} "$out/repositories.json"
    cp ${profileMetadata} "$out/profile.json"
    cp ${metadata} "$out/metadata.json"
  ''
