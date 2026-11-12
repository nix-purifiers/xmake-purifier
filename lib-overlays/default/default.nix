# SPDX-License-Identifier: MIT
{ ... }:
{
  overlay = final: prev: {
    xmake-purifier = (prev.xmake-purifier or { }) // {
      readLock = import ./read-lock.nix { lib = final; };
    };
  };
}
