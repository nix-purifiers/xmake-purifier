# SPDX-License-Identifier: MIT
{ pkgs, self }:
let
  built = pkgs.xmake-purifier.buildXmakeProject { src = ../examples/minimal; };
in
{
  native-projections =
    let
      ffi = pkgs.xmake-purifier.buildXmakeProject {
        src = ./fixtures/ffi;
        doCheck = false;
        nativeLibraries.proof = "lib/libproof${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
      };
    in
    import ./native-consumer.nix {
      inherit pkgs;
      projection = ffi.projections;
      library = "proof";
      symbol = "answer";
    };
  cli = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  environment = built.packages.environment;
  project = built.tests;
  release = built.release;
}
