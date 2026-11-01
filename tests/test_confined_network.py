"""Every confined process has its own network; the outside is reached only
through leerie's egress proxy (DESIGN §6 *A confined process has its own
network; the outside is reached only through leerie's proxy*).

Review round 21's execution H2: `--isolate` shared the network namespace,
so a loopback server one confined process started (a repository's
trust-auth test PostgreSQL) was reachable by every other one, and acted with
its creator's grant — a worker whose grant was only its own tree wrote into
another tree through `COPY … TO PROGRAM`, and a second measurement attached
to the first one's server and lost it. The proof is turned round here.

The proxy's own tests run anywhere. The namespace tests need a runtime that
can isolate (the container under the `leerie-container` profile); they skip
on a host that restricts user namespaces (`isolation_error()`), and need
their state outside `/tmp` (`--basetemp=/leerie-state/...`)."""
from __future__ import annotations

import asyncio
import http.server
import importlib.util
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.test_worker_confinement import _St, _grants, run  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_net", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LL = _shim_module()
_WHY = LL.isolation_error() if LL.abi() >= LL.MIN_ABI else "no Landlock"
needs_isolation = pytest.mark.skipif(
    _WHY is not None,
    reason=f"this host cannot isolate ({_WHY}); run in the container under "
           "the leerie-container profile")

DEV = ["--rw", "/dev/null", "--rw", "/dev/urandom"]


@pytest.fixture
def proxy(leerie, monkeypatch):
    """A fresh egress proxy of this process's, in its own 0700 directory."""
    monkeypatch.setattr(leerie, "_EGRESS", None)
    p = leerie._egress_server()
    yield p
    if p._loop is not None:
        p._loop.call_soon_threadsafe(p._loop.stop)


def _ask(sock_path: str, request: bytes, *, read_all: bool = False) -> bytes:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(20)
    s.connect(sock_path)
    s.sendall(request)
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
        if not read_all and b"\r\n\r\n" in out:
            break
    s.close()
    return out


# --- the proxy: refuses what is inside the container ------------------------


@pytest.mark.parametrize("addr,why", [
    ("127.0.0.1", "loopback"), ("127.8.9.10", "loopback"), ("::1", "loopback"),
    ("::ffff:127.0.0.1", "loopback"), ("169.254.169.254", "link-local"),
    ("fe80::1", "link-local"), ("0.0.0.0", "unspecified"),
    ("::", "unspecified"), ("224.0.0.1", "multicast"),
    ("10.4.0.47", "own"), ("93.184.215.14", None), ("10.9.9.9", None),
    ("2606:4700::1", None)])
def test_the_refusal_names_every_address_inside_the_container(leerie, addr,
                                                               why):
    got = leerie._egress_refusal(addr, {"10.4.0.47"})
    if why is None:
        assert got is None
    else:
        assert got is not None and why in got


def test_own_addresses_come_from_the_kernels_tables(leerie):
    own = leerie._own_addresses()
    assert "127.0.0.1" in own
    for ip in own:
        import ipaddress
        ipaddress.ip_address(ip)


def test_connect_to_a_loopback_destination_is_refused(leerie, proxy):
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    lst.settimeout(0.5)
    port = lst.getsockname()[1]
    try:
        for target in (f"127.0.0.1:{port}", f"localhost:{port}",
                       f"[::1]:{port}"):
            got = _ask(str(proxy.socket_path),
                       f"CONNECT {target} HTTP/1.1\r\n\r\n".encode(),
                       read_all=True)
            assert got.startswith(b"HTTP/1.1 403 "), (target, got)
            assert b"loopback" in got
        got = _ask(str(proxy.socket_path),
                   f"GET http://127.0.0.1:{port}/x HTTP/1.1\r\nHost: a\r\n"
                   "\r\n".encode(), read_all=True)
        assert got.startswith(b"HTTP/1.1 403 ")
        with pytest.raises(OSError):
            lst.accept()
        assert proxy.refusals == 4
    finally:
        lst.close()


def _allow_loopback(leerie, monkeypatch):
    """Stand-in for an outside host: the test's own loopback, allowed."""
    real = leerie._egress_refusal
    monkeypatch.setattr(leerie, "_egress_refusal",
                        lambda a, own: None if a == "127.0.0.1"
                        else real(a, own))


def test_connect_tunnels_to_an_allowed_destination(leerie, proxy,
                                                    monkeypatch):
    _allow_loopback(leerie, monkeypatch)
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    port = lst.getsockname()[1]

    def echo():
        c, _ = lst.accept()
        c.sendall(b"ECHO:" + c.recv(100))
        c.close()
    t = threading.Thread(target=echo)
    t.start()
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(20)
    s.connect(str(proxy.socket_path))
    s.sendall(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
    head = s.recv(4096)
    assert head == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    s.sendall(b"payload")
    assert s.recv(100) == b"ECHO:payload"
    s.close()
    t.join(10)
    lst.close()


def test_a_plain_http_request_is_rewritten_to_origin_form(leerie, proxy,
                                                          monkeypatch):
    _allow_loopback(leerie, monkeypatch)
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.handle_request)
    t.start()
    port = srv.server_address[1]
    got = _ask(str(proxy.socket_path),
               (f"GET http://127.0.0.1:{port}/a/b?q=1 HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\nProxy-Connection: keep-alive\r\n"
                "Proxy-Authorization: Basic eDp5\r\nX-Kept: 1\r\n\r\n"
                ).encode(), read_all=True)
    t.join(10)
    srv.server_close()
    assert got.startswith(b"HTTP/1.0 200") and got.endswith(b"ok")
    assert seen["path"] == "/a/b?q=1"
    assert seen["headers"]["x-kept"] == "1"
    assert seen["headers"]["connection"] == "close"
    assert "proxy-connection" not in seen["headers"]
    assert "proxy-authorization" not in seen["headers"]


def test_anything_but_connect_or_absolute_http_is_a_bad_request(proxy):
    for req in (b"GET /x HTTP/1.1\r\n\r\n",
                b"GET https://a.example/ HTTP/1.1\r\n\r\n",
                b"CONNECT nohost HTTP/1.1\r\n\r\n", b"junk\r\n\r\n"):
        assert _ask(str(proxy.socket_path), req,
                    read_all=True).startswith(b"HTTP/1.1 400 "), req


def test_forward_pipes_a_socket_to_the_orchestrators_port(proxy):
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    port = lst.getsockname()[1]

    def serve():
        c, _ = lst.accept()
        c.sendall(b"strict:" + c.recv(10))
        c.close()
    t = threading.Thread(target=serve)
    t.start()
    path = proxy.forward(port)
    assert proxy.forward(port) == path
    assert Path(path).parent == proxy.dir
    assert _ask(path, b"hello", read_all=True) == b"strict:hello"
    t.join(10)
    lst.close()


def test_the_proxy_lives_in_a_private_directory_outside_every_grant(
        leerie, proxy):
    st_ = os.stat(proxy.dir)
    assert oct(st_.st_mode & 0o777) == "0o700"
    assert proxy.dir.parent == leerie.PRIVATE_TMP_ROOT


# --- who gets egress ---------------------------------------------------------


def _opts(argv: list[str]) -> list[str]:
    return argv[:argv.index("--")]


def test_only_grants_with_egress_name_the_proxy(leerie, proxy, monkeypatch):
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    no = leerie.WorkerGrants(("/dev/null",), ("/",))
    yes = no._replace(egress=True)
    assert "--egress" not in _opts(leerie._confined_argv(no, ["true"]))
    o = _opts(leerie._confined_argv(yes, ["true"]))
    assert o[o.index("--egress") + 1] == str(proxy.socket_path)
    assert o.index("--isolate") < o.index("--egress")
    assert "--forward" not in o
    # Unisolated (a test, a host harness): no namespace, no relay.
    monkeypatch.delenv(leerie.ISOLATE_ENV)
    assert "--egress" not in leerie._confined_argv(yes, ["true"])


def test_the_strict_output_proxy_port_is_relayed(leerie, proxy, monkeypatch):
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    port = lst.getsockname()[1]
    monkeypatch.setattr(leerie, "_STRICT_PROXY",
                        type("P", (), {"port": port})())
    o = _opts(leerie._confined_argv(
        leerie.WorkerGrants(("/dev/null",), ("/",), egress=True), ["true"]))
    fwd = o[o.index("--forward") + 1]
    assert fwd == f"{port}={proxy.dir}/fwd-{port}.sock"
    lst.close()


def test_kinds_that_fetch_get_egress_and_a_measurement_does_not(leerie, run):
    st = _St(run["user"], run["run_dir"])
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    got = {k: leerie._measurement_grants(st, view, k).egress
           for k in (leerie.MEASURE_KIND, leerie.SETUP_HOOK_KIND,
                     leerie.TOOLCHAIN_KIND, leerie.STORE_FETCH_KIND)}
    assert got == {leerie.MEASURE_KIND: False, leerie.SETUP_HOOK_KIND: True,
                   leerie.TOOLCHAIN_KIND: True,
                   leerie.STORE_FETCH_KIND: True}
    assert leerie._heldout_grants(st, view).egress is False
    for key in ("implementer", "classifier", "integrator"):
        assert _grants(leerie, run, schema_key=key).egress is True


# --- the shim's options ------------------------------------------------------


def test_egress_and_forward_need_isolate_and_absolute_sockets():
    assert LL._parse(["--egress", "/s", "--", "true"]) == (
        "landlock_exec.py: --egress and --forward need --isolate")
    for bad in (["--isolate", "--egress", "rel", "--", "true"],
                ["--isolate", "--forward", "0=/s", "--", "true"],
                ["--isolate", "--forward", "70000=/s", "--", "true"],
                ["--isolate", "--forward", "80=rel", "--", "true"],
                ["--isolate", "--forward", "x=/s", "--", "true"]):
        assert isinstance(LL._parse(bad), str) and "bad" in LL._parse(bad)
    o = LL._parse(["--isolate", "--egress", "/e.sock", "--forward",
                   "4000=/f.sock", "--", "true"])
    assert o["egress"] == "/e.sock" and o["forward"] == [(4000, "/f.sock")]


def test_proxy_env_names_the_relay_and_leaves_loopback_direct():
    env = LL.proxy_env(4321)
    for v in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy",
              "http_proxy", "all_proxy"):
        assert env[v] == "http://127.0.0.1:4321"
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,127.0.0.1,::1"
    assert env["NODE_USE_ENV_PROXY"] == "1"


# --- in a namespace: H2 turned round -------------------------------------------


def _isolated(cwd: Path, rw: list[Path], code: str, *, egress: str | None
              = None, forward=(), timeout: float = 60
              ) -> subprocess.Popen:
    opts = ["--isolate", *DEV]
    if egress:
        opts += ["--egress", egress]
    for port, sock in forward:
        opts += ["--forward", f"{port}={sock}"]
    for p in rw:
        opts += ["--rw", str(p)]
    return subprocess.Popen(
        [sys.executable, "-I", "-S", str(SHIM), *opts, "--ro", "/", "--",
         sys.executable, "-I", "-c", textwrap.dedent(code)],
        cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)


# A "server a repository starts": on any connection it writes, with its
# own grant, the file the client names — as `COPY … TO PROGRAM` did.
_SERVER = """
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", {port}))
s.listen(5)
print("LISTENING", flush=True)
s.settimeout({life})
try:
    while True:
        c, _ = s.accept()
        path = c.recv(1000).decode()
        open(path, "w").write("written by the server")
        c.sendall(b"DONE")
        c.close()
except socket.timeout:
    pass
"""


def _wait_line(p: subprocess.Popen, want: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if want in line:
            return
        if not line and p.poll() is not None:
            raise AssertionError(p.stderr.read())
    raise AssertionError(f"no {want!r}")


@pytest.fixture
def root(tmp_path):
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("grants beneath /tmp are carried, not bound: run with "
                    "--basetemp outside /tmp")
    return tmp_path


@needs_isolation
def test_h2_a_worker_cannot_reach_another_commands_loopback_server(
        leerie, proxy, root):
    """The measurement's server writes with the measurement's grant; the
    worker, whose grant is only its own tree, asks it to write into the
    measured tree — directly, and through the egress proxy. Neither
    reaches it, and the tree is unchanged."""
    tree, own = root / "measured", root / "worker"
    tree.mkdir()
    own.mkdir()
    port = 55432
    m = _isolated(tree, [tree], _SERVER.format(port=port, life=20))
    try:
        _wait_line(m, "LISTENING")
        target = tree / "PWNED"
        w = _isolated(own, [own], f"""
            import os, socket
            try:
                c = socket.create_connection(("127.0.0.1", {port}), 3)
                c.sendall({str(target)!r}.encode()); print("direct", c.recv(9))
            except OSError as e:
                print("direct refused", e.errno)
            host, p = os.environ["HTTPS_PROXY"].rsplit("/", 1)[1].split(":")
            c = socket.create_connection((host, int(p)), 10)
            c.sendall(b"CONNECT 127.0.0.1:{port} HTTP/1.1\\r\\n\\r\\n")
            print("proxy", c.recv(200).split(b"\\r\\n")[0].decode())
            """, egress=str(proxy.socket_path))
        out, err = w.communicate(timeout=60)
        assert w.returncode == 0, err
        assert "direct refused 111" in out, out
        assert "proxy HTTP/1.1 403 Forbidden" in out, out
        assert not target.exists()
    finally:
        m.kill()
        m.communicate()


@needs_isolation
def test_two_concurrent_commands_each_have_the_whole_port_space(root):
    """The second measurement binds the same fixed port, gets its own
    server, and keeps it after the first ends — it never attaches to the
    first one's."""
    a, b = root / "a", root / "b"
    a.mkdir()
    b.mkdir()
    port = 55432
    m1 = _isolated(a, [a], _SERVER.format(port=port, life=3))
    try:
        _wait_line(m1, "LISTENING")
        # Shared, this bind is EADDRINUSE (or, as the repository's script
        # did, the second command finds the first one's server and uses it).
        m2 = _isolated(b, [b], _SERVER.format(port=port, life=8))
        _wait_line(m2, "LISTENING")
        m1.communicate(timeout=30)
        assert m2.poll() is None
    finally:
        for p in (m1, m2):
            p.kill()
            p.communicate()


@needs_isolation
def test_a_command_without_egress_has_loopback_and_nothing_else(root):
    p = _isolated(root, [root], """
        import os, socket
        print("IFACES", [n for _i, n in socket.if_nameindex()])
        print("PROXY", sorted(k for k in os.environ if "proxy" in k.lower()))
        for addr in (("1.1.1.1", 443), ("8.8.8.8", 53)):
            try:
                socket.create_connection(addr, 3); print("REACHED", addr)
            except OSError as e:
                print("unreachable", e.errno)
        try:
            socket.getaddrinfo("example.com", 443); print("RESOLVED")
        except OSError:
            print("no resolver")
        """)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    assert "IFACES ['lo']" in out
    assert "PROXY []" in out
    assert "REACHED" not in out and "RESOLVED" not in out, out


@needs_isolation
def test_the_relay_reaches_the_proxy_and_ends_with_the_command(
        leerie, proxy, root, monkeypatch):
    """Through the relay: an allowed destination is tunnelled, a forwarded
    port reaches the orchestrator's listener, and when the command exits no
    relay process is left."""
    _allow_loopback(leerie, monkeypatch)
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(4)
    port = lst.getsockname()[1]

    def serve():
        for _ in range(2):
            c, _ = lst.accept()
            c.sendall(b"OUTER:" + c.recv(20))
            c.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    p = _isolated(root, [root], f"""
        import os, socket
        host, rp = os.environ["HTTPS_PROXY"].rsplit("/", 1)[1].split(":")
        c = socket.create_connection((host, int(rp)), 10)
        c.sendall(b"CONNECT 127.0.0.1:{port} HTTP/1.1\\r\\n\\r\\n")
        head = c.recv(200)
        c.sendall(b"tunnel")
        print("TUNNEL", head.split(b"\\r\\n")[0].decode(), c.recv(50))
        f = socket.create_connection(("127.0.0.1", {port}), 10)
        f.sendall(b"forward")
        print("FORWARD", f.recv(50))
        shim = os.getppid()
        kids = open(f"/proc/{{shim}}/task/{{shim}}/children").read().split()
        print("RELAY", [k for k in kids if int(k) != os.getpid()])
        """, egress=str(proxy.socket_path),
        forward=[(port, proxy.forward(port))])
    out, err = p.communicate(timeout=60)
    lst.close()
    assert p.returncode == 0, err
    assert "TUNNEL HTTP/1.1 200 Connection Established b'OUTER:tunnel'" \
        in out, out
    assert "FORWARD b'OUTER:forward'" in out, out
    relay = json.loads(out.split("RELAY ")[1].split("\n")[0].replace("'",
                                                                      '"'))
    assert len(relay) == 1, out
    assert not os.path.exists(f"/proc/{relay[0]}")


@needs_isolation
def test_preflight_checks_the_relay_and_fails_closed(leerie, proxy,
                                                     monkeypatch):
    leerie._require_egress_relay()
    # The shim built without a network namespace (the defect): the
    # orchestrator's loopback is reachable, and preflight stops the run.
    real = subprocess.run

    def shared_net(argv, *a, **kw):
        if "--isolate" in argv:
            argv = [x for x in argv if x != "--isolate"]
            i = argv.index("--egress")
            del argv[i:i + 2]
            argv[argv.index("--") + 1:argv.index("--") + 1] = [
                "env", f"HTTPS_PROXY=http://127.0.0.1:1"]
        return real(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "run", shared_net)
    with pytest.raises(SystemExit):
        leerie._require_egress_relay()


def test_preflight_names_each_failure(leerie, proxy, monkeypatch):
    """Without a namespace: the probe's report is read, not its exit."""
    msgs = []
    monkeypatch.setattr(leerie, "die", lambda m, **k: msgs.append(m)
                        or (_ for _ in ()).throw(SystemExit(1)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps(
                            {"proxy": "HTTP/1.1 200 Connection Established",
                             "outer_loopback": "reached"}) + "\n", ""))
    with pytest.raises(SystemExit):
        leerie._require_egress_relay()
    assert "did not refuse a loopback destination" in msgs[0]
    assert "'reached' from a confined process's namespace" in msgs[0]


# --- the held-out placement follows no link (round 21 isolation R6) ---------


@needs_isolation
def test_r6_a_placed_file_never_follows_a_link_in_the_view(root):
    v = root / "view"
    v.mkdir()
    worker_tree = root / "worker-tree"
    worker_tree.mkdir()
    (v / "tests").symlink_to(worker_tree)
    (v / "deep").mkdir()
    (v / "deep" / "in").symlink_to(worker_tree)
    held = root / "held.sh"
    secret = f"HELD_{uuid.uuid4().hex[:12]}"
    held.write_text(f"# {secret}\n")
    for rel in ("tests/held.sh", "deep/in/held.sh", "tests/new/held.sh"):
        r = subprocess.run(
            [sys.executable, "-I", "-S", str(SHIM), "--sandbox", "--view",
             str(v), "--place", f"{held}={rel}", "--rw", str(v), *DEV,
             "--ro", "/", "--", "true"],
            cwd=str(v), capture_output=True, text=True, timeout=60)
        assert r.returncode == 126, (rel, r.stderr)
        assert "is a link" in r.stderr, r.stderr
    assert list(worker_tree.iterdir()) == []
    # A real directory on the path still takes the file.
    r = subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), "--sandbox", "--view",
         str(v), "--place", f"{held}=deep/real/held.sh", "--rw", str(v),
         *DEV, "--ro", "/", "--", "cat", "deep/real/held.sh"],
        cwd=str(v), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and secret in r.stdout, r.stderr
    assert not (v / "deep" / "real").exists()


def test_r6_the_emulator_refuses_a_linked_directory_too(tmp_path):
    v = tmp_path / "view"
    v.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (v / "tests").symlink_to(out)
    held = tmp_path / "held.sh"
    held.write_text("x\n")
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tests" /
                             "heldout_sandbox_emulator.py"),
         "--view", str(v), "--place", f"{held}=tests/held.sh", "--",
         "true"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 126 and "tests/held.sh" in r.stderr
    assert list(out.iterdir()) == []
