#!/usr/bin/env python3
"""scripts/remote/registry_image.py — read what a registry holds under an
image reference (Docker Registry HTTP API v2), for build-push.sh: the digest
a tag names, and whether a repository image is leerie's layout.

Usage:
  registry_image.py digest <ref>
      Print the digest of the manifest <ref> names (`sha256:<hex>`).
  registry_image.py layout <base-ref> <ref>
      Exit 0 when <ref> is <base-ref>'s layers plus exactly one, with
      <base-ref>'s configuration (`.config`, apart from `Image`): the rule
      the launcher's `_check_repo_image` applies to a local repository image
      (DESIGN §6 *The orchestrator's root is leerie's; a repository's root is
      only a confined process's*), labels aside: a builder may add its own,
      and a label runs nothing. Exit 1, naming the difference, when not.

A reference is `<host>/<repository>[:<tag>|@<digest>]`. The registry password
is read from stdin (empty for none) and offered as user `x`, the form Fly's
registry takes (`docker login registry.fly.io -u x -p <token>`), through the
registry's own challenge (Basic, or a Bearer token from its realm). A host
that is `localhost` or a 127.0.0.0/8 address is spoken to over plain HTTP,
as Docker treats those registries as insecure by default; every other over
HTTPS. A multi-platform index is resolved to its linux/amd64 manifest (the
platform Fly Machines run). Exit 2 on any error reaching the registry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
INDEX_TYPES = MANIFEST_TYPES[:2]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
LIMIT = 4 << 20
PLATFORM = ("linux", "amd64")


class RegistryError(Exception):
    pass


def parse_ref(ref: str) -> tuple[str, str, str]:
    host, sep, rest = ref.partition("/")
    if not sep or not rest or not (("." in host) or (":" in host)
                                   or host == "localhost"):
        raise RegistryError(f"not a registry reference: {ref!r}")
    if "@" in rest:
        repo, _, ident = rest.partition("@")
        if not DIGEST.fullmatch(ident):
            raise RegistryError(f"not a sha256 digest: {ident!r}")
    else:
        repo, sep, ident = rest.rpartition(":")
        if not sep or "/" in ident:
            raise RegistryError(f"no tag or digest in {ref!r}")
    if not re.fullmatch(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*", repo):
        raise RegistryError(f"not a repository name: {repo!r}")
    return host, repo, ident


def _scheme(host: str) -> str:
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name == "localhost" or re.fullmatch(r"127(?:\.\d{1,3}){3}", name):
        return "http"
    return "https"


class Registry:
    def __init__(self, host: str, password: str) -> None:
        self.base = f"{_scheme(host)}://{host}"
        self.password = password
        self.auth: str | None = None

    def _open(self, path: str, accept: tuple[str, ...]):
        req = urllib.request.Request(self.base + path)
        if accept:
            req.add_header("Accept", ", ".join(accept))
        if self.auth:
            # Not carried to a redirect: a blob is often served from
            # another host that takes its own credentials in the URL.
            req.add_unredirected_header("Authorization", self.auth)
        return urllib.request.urlopen(req, timeout=60)

    def _challenge(self, header: str, repo: str) -> None:
        scheme, _, params = header.partition(" ")
        basic = base64.b64encode(f"x:{self.password}".encode()).decode()
        if scheme.lower() == "basic":
            self.auth = f"Basic {basic}"
            return
        if scheme.lower() != "bearer":
            raise RegistryError(f"unknown authentication scheme {scheme!r}")
        fields = dict(re.findall(r'(\w+)="([^"]*)"', params))
        realm = fields.get("realm", "")
        if urllib.parse.urlsplit(realm).scheme not in ("https", "http"):
            raise RegistryError(f"no token realm in {header!r}")
        query = {"scope": fields.get("scope") or f"repository:{repo}:pull"}
        if fields.get("service"):
            query["service"] = fields["service"]
        req = urllib.request.Request(
            realm + "?" + urllib.parse.urlencode(query))
        if self.password:
            req.add_unredirected_header("Authorization", f"Basic {basic}")
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read(LIMIT))
        token = body.get("token") or body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistryError("the token realm returned no token")
        self.auth = f"Bearer {token}"

    def get(self, path: str, repo: str,
            accept: tuple[str, ...] = ()) -> tuple[bytes, dict]:
        for attempt in (0, 1):
            try:
                with self._open(path, accept) as r:
                    data = r.read(LIMIT + 1)
                    headers = {k.lower(): v for k, v in r.headers.items()}
            except urllib.error.HTTPError as e:
                challenge = e.headers.get("WWW-Authenticate", "")
                if e.code == 401 and attempt == 0 and challenge:
                    self._challenge(challenge, repo)
                    continue
                raise RegistryError(f"{self.base}{path}: HTTP {e.code}")
            except (urllib.error.URLError, OSError) as e:
                raise RegistryError(f"{self.base}{path}: {e}")
            if len(data) > LIMIT:
                raise RegistryError(f"{self.base}{path}: larger than {LIMIT}")
            return data, headers
        raise RegistryError(f"{self.base}{path}: not authorised")

    def manifest(self, repo: str, ident: str) -> tuple[dict, str, str]:
        data, headers = self.get(f"/v2/{repo}/manifests/{ident}", repo,
                                 MANIFEST_TYPES)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if DIGEST.fullmatch(ident) and digest != ident:
            raise RegistryError(f"manifest {ident} has digest {digest}")
        told = headers.get("docker-content-digest", digest)
        if told != digest:
            raise RegistryError(f"registry says {told}, body is {digest}")
        try:
            body = json.loads(data)
        except ValueError:
            raise RegistryError(f"manifest {repo}:{ident} is not JSON")
        kind = body.get("mediaType") or headers.get("content-type", "")
        return body, kind.split(";")[0].strip(), digest

    def image_config(self, repo: str, ident: str) -> dict:
        body, kind, _ = self.manifest(repo, ident)
        if kind in INDEX_TYPES or "manifests" in body:
            picks = [m.get("digest") for m in body.get("manifests", [])
                     if (m.get("platform") or {}).get("os") == PLATFORM[0]
                     and (m.get("platform") or {}).get("architecture")
                     == PLATFORM[1]]
            if len(picks) != 1 or not DIGEST.fullmatch(str(picks[0])):
                raise RegistryError(
                    f"{repo}:{ident} has no single {'/'.join(PLATFORM)} image")
            body, _, _ = self.manifest(repo, picks[0])
        cdigest = (body.get("config") or {}).get("digest", "")
        if not DIGEST.fullmatch(str(cdigest)):
            raise RegistryError(f"{repo}:{ident} names no config digest")
        data, _ = self.get(f"/v2/{repo}/blobs/{cdigest}", repo)
        if "sha256:" + hashlib.sha256(data).hexdigest() != cdigest:
            raise RegistryError(f"config blob {cdigest} does not match")
        try:
            config = json.loads(data)
        except ValueError:
            raise RegistryError(f"config blob {cdigest} is not JSON")
        if not isinstance(config, dict):
            raise RegistryError(f"config blob {cdigest} is not an object")
        return config


def layout_problem(base: dict, image: dict) -> str | None:
    bl = (base.get("rootfs") or {}).get("diff_ids")
    il = (image.get("rootfs") or {}).get("diff_ids")
    if not (isinstance(bl, list) and isinstance(il, list) and bl):
        return "a layer list is missing"
    if len(il) != len(bl) + 1 or il[:len(bl)] != bl:
        return (f"its {len(il)} layers are not the base's {len(bl)} "
                "plus one")
    bc, ic = base.get("config"), image.get("config")
    if not (isinstance(bc, dict) and isinstance(ic, dict)):
        return "a configuration is missing"
    if ({k: v for k, v in bc.items() if k not in ("Image", "Labels")}
            != {k: v for k, v in ic.items() if k not in ("Image", "Labels")}):
        return "its configuration is not the base's"
    return None


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "digest":
        refs = [argv[1]]
    elif len(argv) == 3 and argv[0] == "layout":
        refs = argv[1:]
    else:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    password = sys.stdin.read(LIMIT).strip()
    try:
        parsed = [parse_ref(r) for r in refs]
        registries: dict[str, Registry] = {}

        def reg(host: str) -> Registry:
            if host not in registries:
                registries[host] = Registry(host, password)
            return registries[host]

        if argv[0] == "digest":
            host, repo, ident = parsed[0]
            print(reg(host).manifest(repo, ident)[2])
            return 0
        (bh, br, bi), (ih, ir, ii) = parsed
        problem = layout_problem(reg(bh).image_config(br, bi),
                                 reg(ih).image_config(ir, ii))
    except RegistryError as e:
        print(f"registry_image: {e}", file=sys.stderr)
        return 2
    if problem:
        print(f"registry_image: {refs[1]} is not {refs[0]} plus its worker "
              f"root: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
