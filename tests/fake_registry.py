"""A Docker Registry HTTP API v2 on loopback, for the tests of
scripts/remote/registry_image.py and build-push.sh's `--digest-file` /
`--layout-base`.

It serves manifests (by tag and by digest) and blobs for the images a test
puts in it, behind the registry token flow: a request without the token gets
401 with a Bearer challenge whose realm is the server's own `/token`.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
TOKEN = "fake-registry-token"


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeRegistry:
    def __init__(self) -> None:
        self.manifests: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.blobs: dict[str, bytes] = {}
        self.lie_about: set[str] = set()
        self.token_requests: list[str] = []
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, code: int, body: bytes = b"",
                      headers: dict | None = None) -> None:
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path, _, query = self.path.partition("?")
                if path == "/token":
                    registry.token_requests.append(query)
                    self._send(200, json.dumps({"token": TOKEN}).encode())
                    return
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    realm = f"http://{registry.host}/token"
                    self._send(401, headers={"WWW-Authenticate": (
                        f'Bearer realm="{realm}",service="fake",'
                        'scope="repository:testapp:pull"')})
                    return
                parts = path.split("/")
                # /v2/<repo>/manifests/<ref> or /v2/<repo>/blobs/<digest>
                if len(parts) == 5 and parts[1] == "v2":
                    _, _, repo, kind, ref = parts
                    if kind == "manifests" and (repo, ref) in registry.manifests:
                        body, media = registry.manifests[(repo, ref)]
                        told = digest_of(body)
                        if ref in registry.lie_about:
                            told = "sha256:" + "0" * 64
                        self._send(200, body, {"Content-Type": media,
                                               "Docker-Content-Digest": told})
                        return
                    if kind == "blobs" and ref in registry.blobs:
                        self._send(200, registry.blobs[ref])
                        return
                self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def __enter__(self) -> "FakeRegistry":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def put_image(self, repo: str, tag: str, layers: list[str],
                  config: dict) -> str:
        """Store an image with these diff_ids and `.config`; return the
        digest of its manifest."""
        blob = json.dumps({"architecture": "amd64", "os": "linux",
                           "rootfs": {"type": "layers", "diff_ids": layers},
                           "config": config}).encode()
        self.blobs[digest_of(blob)] = blob
        manifest = json.dumps({
            "schemaVersion": 2, "mediaType": OCI_MANIFEST,
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                       "digest": digest_of(blob), "size": len(blob)},
            "layers": []}).encode()
        d = digest_of(manifest)
        self.manifests[(repo, tag)] = (manifest, OCI_MANIFEST)
        self.manifests[(repo, d)] = (manifest, OCI_MANIFEST)
        return d

    def put_index(self, repo: str, tag: str,
                  platforms: dict[tuple[str, str], str]) -> str:
        """Store an index naming each (os, architecture) → manifest digest."""
        index = json.dumps({
            "schemaVersion": 2, "mediaType": OCI_INDEX,
            "manifests": [{"mediaType": OCI_MANIFEST, "digest": d,
                           "size": 1,
                           "platform": {"os": o, "architecture": a}}
                          for (o, a), d in platforms.items()]}).encode()
        d = digest_of(index)
        self.manifests[(repo, tag)] = (index, OCI_INDEX)
        self.manifests[(repo, d)] = (index, OCI_INDEX)
        return d
