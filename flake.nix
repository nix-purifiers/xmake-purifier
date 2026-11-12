# SPDX-License-Identifier: MIT
{
  description = "Locked, Nix-resolved dependencies for Xmake";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.purifier = {
    url = "github:nix-purifiers/purifier";
    flake = false;
  };
  outputs =
    {
      self,
      nixpkgs,
      purifier,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = nixpkgs.lib.genAttrs systems;
      overlay = final: prev: {
        xmake-purifier = final.lib.makeScope final.newScope (scope: {
          nativeXmake = scope.callPackage ./pkgs/native-xmake/default.nix { };
          toolchain = scope.callPackage ./pkgs/toolchain/default.nix { };
          cli = scope.callPackage ./pkgs/cli/default.nix {
            nixpkgsSrc = nixpkgs;
            purifierSrc = purifier;
          };
          buildXmakeProject = scope.callPackage ./pkgs/build-xmake-project/default.nix {
            purifier = import (purifier + "/nix/lib") { inherit (final) lib; };
          };
        });
      };
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
    in
    {
      overlays.default = overlay;
      packages = forAll (system: {
        native = (pkgsFor system).xmake-purifier.nativeXmake;
        default = (pkgsFor system).xmake-purifier.cli;
        cli = self.packages.${system}.default;
      });
      lib.xmake-purifier =
        (nixpkgs.lib.extend (import ./lib-overlays/default { }).overlay).xmake-purifier;
      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/xmake-purifier";
          meta.description = "Lock and prefetch Xmake dependencies";
        };
      });
      checks = forAll (
        system:
        import ./tests {
          pkgs = pkgsFor system;
          inherit self;
        }
      );
      formatter = forAll (system: (pkgsFor system).nixfmt-tree);
    };
}
