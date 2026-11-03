# SPDX-License-Identifier: MIT
{
  project,
  nativeLock,
  companion,
  mode ? "release",
  target,
}:
let
  pkgs = import (/. + "@nixpkgs@") { };
  purifier = import (/. + "@purifier@/nix/lib") { inherit (pkgs) lib; };
  nativeXmake = pkgs.callPackage ./xmake.nix { };
  toolchain = pkgs.callPackage ./toolchain.nix { inherit nativeXmake; };
  args = {
    src = /. + project;
    lockFile = /. + companion;
    requiresLock = /. + nativeLock;
  };
  lock = import ./read-lock.nix { inherit (pkgs) lib; } args;
in
if target == "environment" then
  pkgs.callPackage ./environment.nix { inherit purifier nativeXmake toolchain; } {
    inherit lock mode;
  }
else if target == "release" then
  (pkgs.callPackage ./build-project.nix { inherit purifier nativeXmake toolchain; } (
    args // { inherit mode; }
  )).release
else
  throw "xmake-purifier: unknown target"
