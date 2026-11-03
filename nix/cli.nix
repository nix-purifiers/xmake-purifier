# SPDX-License-Identifier: MIT
{
  lib,
  stdenvNoCC,
  stdenv,
  makeWrapper,
  python3,
  nix,
  gh,
  nativeXmake,
  toolchain,
  cacert,
  gcc,
  nixpkgsSrc,
  purifierSrc,
}:
stdenvNoCC.mkDerivation {
  pname = "xmake-purifier";
  version = "0.1.0";
  src = ../.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    mkdir -p "$out/bin" "$out/lib/xmake-purifier"
    cp -r xmake_purifier nix "$out/lib/xmake-purifier/"
    install -m755 bin/xmake-purifier "$out/bin/xmake-purifier"
    substituteInPlace "$out/lib/xmake-purifier/nix/realize.nix" \
      --replace-fail '@nixpkgs@' '${nixpkgsSrc}' \
      --replace-fail '@purifier@' '${purifierSrc}'
    wrapProgram "$out/bin/xmake-purifier" \
      --prefix PATH : ${
        lib.makeBinPath [
          python3
          nix
          gh
          toolchain
        ]
      } \
      --set-default SSL_CERT_FILE ${cacert}/etc/ssl/certs/ca-bundle.crt \
      --set XMAKE_PURIFIER_NATIVE ${nativeXmake}/bin/xmake \
      --set XMAKE_PURIFIER_XREPO ${nativeXmake}/bin/xrepo \
      --set XMAKE_PURIFIER_PROGRAM_DIR ${nativeXmake}/share/xmake \
      --set XMAKE_PURIFIER_SYSTEM ${stdenv.hostPlatform.system} \
      --set XMAKE_PURIFIER_XMAKE_VERSION ${nativeXmake.version} \
      --set XMAKE_PURIFIER_GCC_VERSION ${gcc.version} \
      --set XMAKE_PURIFIER_SDK_VERSION ${builtins.hashFile "sha256" ./xmake.patch} \
      --set XMAKE_PURIFIER_WRAPPER_BIN "$out/bin"
    makeWrapper "$out/bin/xmake-purifier" "$out/bin/xmake" --add-flags '--native --'
    makeWrapper "$out/bin/xmake-purifier" "$out/bin/xrepo" --add-flags '--native --tool=xrepo --'
    makeWrapper "${toolchain}/bin/clangd" "$out/bin/clangd" \
      --add-flags "--query-driver=${gcc}/bin/*,${toolchain}/bin/gcc,${toolchain}/bin/g++,${toolchain}/bin/cc,${toolchain}/bin/c++,$out/bin/gcc,$out/bin/g++,$out/bin/cc,$out/bin/c++"
    for program in ${toolchain}/bin/*; do
      name=$(basename "$program")
      if [ ! -e "$out/bin/$name" ]; then ln -s "$program" "$out/bin/$name"; fi
    done
  '';
  meta = {
    description = "Native Xmake workflow with locked Nix-resolved dependencies";
    homepage = "https://nix-purifiers.github.io/";
    license = lib.licenses.mit;
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
    mainProgram = "xmake-purifier";
  };
}
