# SPDX-License-Identifier: MIT
{
  symlinkJoin,
  nativeXmake,
  gcc,
  git,
  pkg-config,
  curl,
  gnutar,
  gzip,
  unzip,
  gnumake,
  cmake,
  ninja,
  clang-tools,
}:
symlinkJoin {
  name = "xmake-purifier-toolchain";
  paths = [
    nativeXmake
    gcc
    git
    pkg-config
    curl
    gnutar
    gzip
    unzip
    gnumake
    cmake
    ninja
    clang-tools
  ];
}
