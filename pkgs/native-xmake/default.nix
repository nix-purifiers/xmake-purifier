# SPDX-License-Identifier: MIT
{ xmake }:
xmake.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./xmake.patch ];
})
