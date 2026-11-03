#!/usr/bin/env bash
set -euo pipefail
system=$(nix eval --impure --raw --expr builtins.currentSystem)
nix build --no-link --rebuild ".#checks.$system.environment" ".#checks.$system.project" ".#checks.$system.release"
