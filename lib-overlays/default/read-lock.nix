# SPDX-License-Identifier: MIT
{ lib }:
{
  src,
  lockFile ? src + "/xmake-purifier.lock.json",
  requiresLock ? src + "/xmake-requires.lock",
}:
let
  exists = builtins.pathExists lockFile && builtins.pathExists requiresLock;
  raw = if exists then builtins.fromJSON (builtins.readFile lockFile) else { };
  data = if builtins.isAttrs raw then raw else { };
  safe = s: builtins.isString s && builtins.match "[A-Za-z0-9_-]+" s != null;
  relative =
    s:
    builtins.isString s
    && s != ""
    && !(lib.hasPrefix "/" s)
    && !(builtins.elem ".." (lib.splitString "/" s));
  url = s: builtins.isString s && builtins.match "https://[^/@?#]+/[^@?#]+" s != null;
  sri = s: builtins.isString s && builtins.match "sha256-[A-Za-z0-9+/]{43}=" s != null;
  manifests = data.manifests or { };
  manifestsOK =
    builtins.isAttrs manifests
    && manifests ? "xmake.lua"
    && builtins.all (
      name:
      relative name
      && builtins.isAttrs manifests.${name}
      && builtins.isString (manifests.${name}.content or null)
      && builtins.pathExists (src + "/${name}")
      && builtins.hashFile "sha256" (src + "/${name}") == (manifests.${name}.sha256 or null)
      && builtins.hashString "sha256" manifests.${name}.content == manifests.${name}.sha256
    ) (builtins.attrNames manifests);
  entryOK =
    e:
    builtins.isAttrs e
    && sri (e.hash or null)
    && (
      if e ? git then
        builtins.isAttrs e.git
        && url (e.git.url or null)
        && builtins.match "[0-9a-f]{40}" (e.git.rev or "") != null
        && (e.git.submodules or null) == false
        && (e.git.leaveDotGit or null) == false
        && (e.git.commitObject or null) == null
        && builtins.isString (e.path or null)
        && builtins.match "repositories/[A-Za-z0-9_.-]+/[0-9a-f]{40}" e.path != null
      else
        builtins.isString (e.path or null)
        && builtins.match "[0-9a-f]{64}" e.path != null
        && url (e.url or null)
        && (e.mirrors or null) == [ ]
    );
  repoOK =
    r:
    builtins.isAttrs r
    && builtins.isString (r.name or null)
    && builtins.match "[A-Za-z0-9_.-]+" r.name != null
    && !(builtins.elem r.name [
      "."
      ".."
    ])
    && url (r.url or null)
    && (!(r ? branch) || builtins.isString r.branch)
    && builtins.isString (r.commit or null)
    && builtins.match "[0-9a-f]{40}" r.commit != null;
  packageOK =
    p:
    builtins.isAttrs p
    && safe (p.name or null)
    && builtins.isString (p.version or null)
    && builtins.isString (p.buildhash or null)
    && builtins.match "[0-9a-f]{32}" p.buildhash != null
    && repoOK (p.repo or null)
    && builtins.isAttrs (p.requires or null);
  profileOK =
    p:
    builtins.isAttrs p
    && builtins.elem (p.system or null) [
      "x86_64-linux"
      "aarch64-linux"
    ]
    && safe (p.mode or null)
    && builtins.isString (p.nativeLock or null)
    && builtins.isList (p.entries or null)
    && builtins.all entryOK p.entries
    && builtins.isList (p.repositories or null)
    && builtins.all repoOK p.repositories
    && builtins.isList (p.packages or null)
    && builtins.all packageOK p.packages
    && builtins.isAttrs (p.manifests or null)
    && p.manifests ? "xmake.lua"
    && builtins.all (
      name: relative name && builtins.hasAttr name manifests && p.manifests.${name} == manifests.${name}
    ) (builtins.attrNames p.manifests);
  profiles = data.profiles or { };
  structure =
    (data.version or null) == 1
    && builtins.isAttrs profiles
    && profiles != { }
    && builtins.all (
      key:
      let
        p = profiles.${key};
      in
      profileOK p && key == "${p.system}:${p.mode}"
    ) (builtins.attrNames profiles)
    && builtins.isAttrs (data.toolVersions or null)
    && builtins.all (name: builtins.isString (data.toolVersions.${name} or null)) [
      "xmake"
      "gcc"
      "sdk"
    ];
  problems =
    lib.optional (!exists) "native Xmake and companion locks are required"
    ++ lib.optional (!structure) "invalid native Xmake profiles"
    ++ lib.optional (!manifestsOK) "Xmake project inputs changed; run xmake-purifier lock"
    ++ lib.optional (
      exists && builtins.hashFile "sha256" requiresLock != (data.nativeHash or null)
    ) "native Xmake lock changed; run xmake-purifier lock";
in
{
  purifier = "xmake";
  inherit data problems;
  entries =
    if structure then lib.unique (lib.concatMap (p: p.entries) (builtins.attrValues profiles)) else [ ];
  outputs = {
    packages = [
      "default"
      "environment"
      "project"
    ];
    tests = [ "default" ];
    release = [ "default" ];
  };
}
