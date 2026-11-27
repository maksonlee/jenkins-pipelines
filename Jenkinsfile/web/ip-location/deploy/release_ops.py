#!/usr/bin/env python3
"""Verify a formal IP Location release and deploy only its application units."""

import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import select
import shlex
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


HOST = "ip.maksonlee.com"
PUBLIC_URL = f"https://{HOST}"
SSH_ORIGIN = "ubuntu@app.maksonlee.com"
ACCOUNT = "ip-location"
INSTALL = Path("/opt/ip-location")
CONFIG = Path("/etc/ip-location/config.toml")
DATA = Path("/var/lib/ip-location")
UNITS = Path("/etc/systemd/system")
RUNTIME_UNITS = Path("/run/systemd/system")
NGINX = Path("/etc/nginx")
CERTBOT = Path("/etc/letsencrypt")
WEB = "ip-location.service"
UPDATE = "ip-location-update.service"
TIMER = "ip-location-update.timer"
LOOPBACK = "127.0.0.1"
PRIVATE_PORT, PUBLIC_PORT = 38171, 38170
MAX_BYTES = 200 * 1024 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}")
BUILD = re.compile(r"[1-9][0-9]{0,8}")
HASH = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,39}")
ASSET = re.compile(r"web/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,}\.(?:css|js|png|svg)")
SLOT = re.compile(r"[0-9a-f]{40}(?:-[1-9][0-9]{0,8})?")
REQUIRED = {"ip-location", "release.json", "web/index.html"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(*args, timeout=30, allowed=(0,)):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    # The updater's failures are in its systemd journal. Keep arbitrary provider output private.
    require(result.returncode in allowed, f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout.strip()


def verify_bundle(args, archive, destination):
    require(archive.is_file() and archive.stat().st_size <= MAX_BYTES, "Missing or oversized release archive")
    require(digest(archive) == args.sha256, "Release archive differs from the approved SHA-256")
    hashes = {}
    total = 0
    with tarfile.open(archive, "r:gz") as package:
        for member in package:
            name = member.name
            require(name in REQUIRED or ASSET.fullmatch(name), f"Unexpected archive path: {name!r}")
            require(member.type == tarfile.REGTYPE and name not in hashes and len(hashes) < 200,
                    "Duplicate or nonregular archive entry")
            mode = 0o755 if name == "ip-location" else 0o644
            require(member.mode == mode and member.size >= 0, f"Unexpected archive mode or size: {name}")
            total += member.size
            require(total <= MAX_BYTES and (name != "release.json" or member.size <= 65536),
                    "Unpacked bundle exceeds its size limit")
            output = destination / name
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with package.extractfile(member) as data, output.open("xb") as stream:
                shutil.copyfileobj(data, stream)
            output.chmod(mode)
            hashes[name] = digest(output)

    require(REQUIRED <= hashes.keys() and any(name.endswith(".js") for name in hashes), "Incomplete release files")
    info = json.loads((destination / "release.json").read_text())
    require(info["schema_version"] == 1 and info["kind"] == "jenkins-release" and
            info["target"] == "aarch64-unknown-linux-musl" and info["commit"] == args.commit and
            info["jenkins_build"] == args.build and info["binary_version"] == f"ip-location {args.version}",
            "Release metadata does not match the reviewed build, commit or version")
    require(HASH.fullmatch(info["source_sha256"]) and set(info["lockfiles"]) == {"Cargo.lock", "web/pnpm-lock.yaml"}
            and all(HASH.fullmatch(value) for value in info["lockfiles"].values()), "Missing source or lockfile digest")
    info_hash = hashes.pop("release.json")
    require(info["sha256"] == hashes, "Bundle files differ from release metadata")
    return {"releaseBuild": args.build, "commit": args.commit, "version": args.version,
            "archiveSha256": args.sha256, "sourceSha256": info["source_sha256"],
            "hostname": HOST, "files": {**hashes, "release.json": info_hash}}


def verify_worker(args):
    archive = args.archive
    require(archive.name == f"ip-location-{args.commit}-arm64.tar.gz", "Unexpected release archive name")
    with archive.with_name(archive.name + ".sha256").open("rb") as stream:
        require(stream.read(256) == f"{args.sha256}  {archive.name}\n".encode(),
                "Release checksum sidecar does not match approved identity")
    with tempfile.TemporaryDirectory(prefix="ip-location-verify-") as work:
        destination = Path(work)
        candidate = verify_bundle(args, archive, destination)
        binary = destination / "ip-location"
        header = command("readelf", "-h", binary)
        require("AArch64" in header and "ELF64" in header and re.search(r"Type:\s+(DYN|EXEC)\b", header),
                "Release binary is not an ARM64 ELF executable")
        require(not re.search(r"^\s*INTERP\b", command("readelf", "-lW", binary), re.M) and
                "(NEEDED)" not in command("readelf", "-d", binary), "Release binary is dynamically linked")
    return candidate


def public_endpoints():
    endpoints = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
    addresses = {(family, address) for family, _, _, _, address in endpoints}
    require(addresses and all(ipaddress.ip_address(address[0]).is_global for _, address in addresses),
            "The approved hostname must have public DNS addresses")
    return sorted(addresses)


def assert_edge_tls(expected_certificate):
    """Validate each published address with normal SNI and the dedicated origin certificate."""
    addresses = public_endpoints()
    context = ssl.create_default_context()
    for family, address in addresses:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(8)
            connection.connect(address)
            with context.wrap_socket(connection, server_hostname=HOST) as tls:
                require(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest() == expected_certificate,
                        "Public TLS did not present the dedicated origin certificate")
    return {"hostname": HOST, "tlsValidated": len(addresses),
            "ipv4": any(family == socket.AF_INET for family, _ in addresses),
            "ipv6": any(family == socket.AF_INET6 for family, _ in addresses)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(origin, path, expected=200, headers=None):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(Request(origin + path, headers=headers or {}), timeout=5)
    except HTTPError as error:
        response = error
    with response:
        require(response.status == expected, f"Unexpected HTTP {response.status} for {origin}{path.split('?')[0]}")
        body = response.read(5 * 1024 * 1024 + 1)
        require(len(body) <= 5 * 1024 * 1024, "Oversized smoke response")
        return body


def local_health(origin):
    deadline = time.monotonic() + 25
    while True:
        try:
            fetch(origin, "/health/live")
            fetch(origin, "/health/ready")
            return
        except (URLError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def external_client_ip(version):
    """Measure the QA worker's egress independently and keep the response in memory."""
    authority = "www.cloudflare.com"
    family = socket.AF_INET if version == 4 else socket.AF_INET6
    connection = http.client.HTTPSConnection(authority, timeout=8)

    def same_family(address, timeout, _source):
        endpoints = socket.getaddrinfo(address[0], address[1], family, socket.SOCK_STREAM)
        require(endpoints, "Independent client-IP probe has no address for the observed family")
        return socket.create_connection(endpoints[0][4][:2], timeout)

    connection._create_connection = same_family
    try:
        connection.request("GET", "/cdn-cgi/trace")
        response = connection.getresponse()
        require(response.status == 200, "Independent client-IP probe failed")
        values = re.findall(rb"(?m)^ip=([0-9a-fA-F:.]+)$", response.read(8192))
        require(len(values) == 1, "Independent client-IP probe returned an unexpected result")
        return ipaddress.ip_address(values[0].decode())
    finally:
        connection.close()


def assert_http(origin, candidate, public=False):
    live, ready = ("/health/live", "/health/ready")
    if public:
        for path in ("/health", live, ready):
            fetch(origin, path, 404)
    else:
        local_health(origin)
    html = fetch(origin, "/")
    hashes = candidate["files"]
    require(hashlib.sha256(html).hexdigest() == hashes["web/index.html"], "Serving different UI bytes than the release")
    entry = re.search(rb'src="(/assets/[A-Za-z0-9_-]+\.js)"', html)
    require(entry, "The release page does not reference a JavaScript entry")
    asset = entry[1].decode()
    require(hashlib.sha256(fetch(origin, asset)).hexdigest() == hashes.get(f"web{asset}"),
            "Serving a different UI entry than the release")
    require(json.loads(fetch(origin, "/api/v1/config"))["schema_version"] == "1.0", "Invalid public API configuration")
    for forbidden in ("/api/v1/deploy-probe-unknown", "/config.toml", "/release.json", "/ip-location"):
        fetch(origin, forbidden, 404)
    for address in ("1.1.1.1", "2606:4700:4700::1111"):
        result = json.loads(fetch(origin, "/api/v1/lookup?" + urlencode({"ip": address})))
        require(result["status"] == "ok" and result["ip"] == address and
                result["ip_version"] == ipaddress.ip_address(address).version and
                all(result[key]["status"] == "ok" and result[key]["source"]["provider"] == "dbip"
                    for key in ("location", "network")), "Public IPv4/IPv6 lookup failed DB-IP provenance checks")
    if public:
        spoof = "198.51.100.222"
        try:
            me = ipaddress.ip_address(json.loads(fetch(origin, "/api/v1/me"))["ip"])
            forged = ipaddress.ip_address(json.loads(fetch(origin, "/api/v1/me", headers={"X-Forwarded-For": spoof}))["ip"])
        except (KeyError, ValueError):
            raise ValueError("Public client-IP probe returned an unexpected response") from None
        require(me == forged and me.is_global and me == external_client_ip(me.version),
                "Client-IP result did not match the independent QA client address with and without a forged header")


def checked_metadata(path, kind, owner=0, mode=None, group=None):
    details = path.lstat()
    require(kind(details.st_mode) and details.st_uid == owner and
            (mode is None or stat.S_IMODE(details.st_mode) == mode) and
            (group is None or details.st_gid == group) and not (details.st_mode & 0o022),
            f"Unexpected ownership, permissions or type: {path}")
    return details


def prepare_directory(path, mode, uid=0, gid=0):
    if not os.path.lexists(path):
        path.mkdir(mode=mode)
        os.chown(path, uid, gid)
    checked_metadata(path, stat.S_ISDIR, uid, mode, gid)


def service_account():
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        command("useradd", "--system", "--user-group", "--home-dir", DATA, "--no-create-home",
                "--shell", "/usr/sbin/nologin", ACCOUNT)
        account = pwd.getpwnam(ACCOUNT)
    group = grp.getgrnam(ACCOUNT)
    require(account.pw_uid > 0 and group.gr_gid > 0 and account.pw_gid == group.gr_gid and
            account.pw_shell in ("/usr/sbin/nologin", "/sbin/nologin") and
            set(os.getgrouplist(ACCOUNT, group.gr_gid)) == {group.gr_gid},
            "Existing ip-location account is not dedicated to the service")
    return account.pw_uid, group.gr_gid


def atomic_file(path, content, mode, group=0):
    with tempfile.TemporaryDirectory(prefix=".ip-location-", dir=path.parent) as work:
        temporary = Path(work) / "file"
        temporary.write_bytes(content)
        temporary.chmod(mode)
        os.chown(temporary, 0, group)
        temporary.replace(path)


def current_release():
    link = INSTALL / "current"
    if not os.path.lexists(link):
        return None
    require(link.is_symlink() and link.lstat().st_uid == 0, "Existing current is not a root-owned symlink")
    target = link.resolve(strict=True)
    require(target.parent == INSTALL / "releases" and SLOT.fullmatch(target.name), "Current points outside release slots")
    checked_metadata(target, stat.S_ISDIR, mode=0o755)
    return target


def switch_release(target):
    link = INSTALL / "current"
    if target is None:
        link.unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(prefix=".switch-", dir=INSTALL) as work:
            temporary = Path(work) / "current"
            temporary.symlink_to(target)
            temporary.replace(link)


@contextmanager
def locked_install():
    prepare_directory(INSTALL, 0o755)
    fd = os.open(INSTALL / ".deploy.lock", os.O_NOFOLLOW | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as lock:
        checked_metadata(INSTALL / ".deploy.lock", stat.S_ISREG, mode=0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def stage(archive, args):
    releases = INSTALL / "releases"
    prepare_directory(releases, 0o755)
    slot = releases / f"{args.commit}-{args.build}"
    with tempfile.TemporaryDirectory(prefix=".incoming-", dir=releases) as work:
        root = Path(work) / "release"
        root.mkdir(mode=0o755)
        candidate = verify_bundle(args, archive, root)
        if os.path.lexists(slot):
            checked_metadata(slot, stat.S_ISDIR, mode=0o755)
            for name, expected in candidate["files"].items():
                path = slot / name
                checked_metadata(path, stat.S_ISREG, mode=0o755 if name == "ip-location" else 0o644)
                require(digest(path) == expected, "Existing release files differ from the verified artifact")
        else:
            root.rename(slot)
    return slot, candidate


def configured_profile(content):
    config = tomllib.loads(content.decode())
    server = config["server"]
    require(server["public_base_url"] == PUBLIC_URL and server["static_dir"] == str(INSTALL / "current/web") and
            config["data"]["root"] == str(DATA) and server["listen"] in
            (f"{LOOPBACK}:{PRIVATE_PORT}", f"{LOOPBACK}:{PUBLIC_PORT}") and config["providers"]["dbip"]["enabled"] is True,
            "Installed config has an unexpected hostname, listen address, data path or DB-IP setting")
    proxy = server["proxy"]
    trust = proxy["trust_x_forwarded_for"]
    require((trust is True and proxy["trusted_proxy_cidrs"] == ["127.0.0.1/32"]) or
            (trust is False and proxy["trusted_proxy_cidrs"] == []), "Unexpected trusted proxy policy")
    maxmind = config["providers"]["maxmind"]["enabled"]
    require(isinstance(maxmind, bool), "Expected a boolean MaxMind provider setting")
    require(server["listen"] != f"{LOOPBACK}:{PRIVATE_PORT}" or trust is False,
            "Private commissioning requires forwarding-header trust disabled")
    return server["listen"], maxmind


def config_for_mode(content, production):
    listen, maxmind = configured_profile(content)
    require(production or listen == f"{LOOPBACK}:{PRIVATE_PORT}", "Refusing to move a public service back to commissioning")
    if production:
        text = content.decode().replace("\r\n", "\n")
        for key, value in (("listen", f'"{LOOPBACK}:{PUBLIC_PORT}"'), ("trust_x_forwarded_for", "true"),
                           ("trusted_proxy_cidrs", '["127.0.0.1/32"]')):
            text, count = re.subn(rf"^{key} = [^#\r\n]+$", f"{key} = {value}", text, flags=re.M)
            require(count == 1, f"Expected one managed config setting: {key}")
        content = text.encode()
        configured_profile(content)
    return content, maxmind


def verify_data(binary, maxmind):
    required = ["--require-all-providers"] if maxmind else []
    result = json.loads(command("runuser", "-u", ACCOUNT, "--", binary, "verify-data", "--config", CONFIG,
                                *required, timeout=120))
    providers = {"dbip", "maxmind"} if maxmind else {"dbip"}
    expected = {(provider, capability) for provider in providers for capability in ("location", "network")}
    checks = result["verification"]
    passed = {(row["provider"], row["capability"]) for row in checks if row["status"] == "passed" and
              ipaddress.ip_address(row["ipv4_sample"]).version == 4 and ipaddress.ip_address(row["ipv6_sample"]).version == 6}
    require(result["complete"] is True and passed == expected, "Independent provider City/ASN IPv4/IPv6 checks failed")
    return checks


def has_local_databases(maxmind):
    providers = ("dbip", "maxmind") if maxmind else ("dbip",)
    return any(
        path.is_file()
        for provider in providers
        for path in (DATA / "providers" / provider).glob("*/*/database.mmdb")
    )


def bootstrap_empty_slot(binary, timer_active):
    """Run the candidate in the existing updater sandbox without switching the public release."""
    directory = RUNTIME_UNITS / f"{UPDATE}.d"
    override = directory / "90-ip-location-bootstrap.conf"
    prepare_directory(directory, 0o755)
    require(not os.path.lexists(override), "A previous IP Location data bootstrap override needs review")
    if timer_active:
        command("systemctl", "stop", TIMER)
    installed = False
    try:
        state = command("systemctl", "show", "-p", "ActiveState", "--value", UPDATE)
        require(state in ("inactive", "failed"), "An IP Location dataset update is already running")
        invocation = f"{binary} update-data --config {CONFIG}"
        atomic_file(override, f"[Service]\nExecStart=\nExecStart={invocation}\n".encode(), 0o644)
        installed = True
        command("systemctl", "daemon-reload")
        actual = command("systemctl", "show", "-p", "ExecStart", "--value", UPDATE)
        require(actual.count("argv[]=") == 1 and f"argv[]={invocation} ;" in actual,
                "The updater did not load the candidate bootstrap override")
        command("systemctl", "start", UPDATE, timeout=1220)
    finally:
        if installed:
            override.unlink()
            command("systemctl", "daemon-reload")
        if timer_active:
            command("systemctl", "start", TIMER)


def service_state(unit):
    enabled = command("systemctl", "is-enabled", unit, allowed=(0, 1, 3, 4))
    active = command("systemctl", "is-active", unit, allowed=(0, 3, 4))
    require(enabled in ("enabled", "disabled") and active in ("active", "inactive", "failed"),
            f"Unknown systemd state for {unit}")
    return enabled == "enabled", active == "active"


def restore_state(unit, state):
    enabled, active = state
    command("systemctl", "enable" if enabled else "disable", unit)
    command("systemctl", "restart" if active else "stop", unit, timeout=60)


def verify_systemd_identity():
    for unit in (WEB, UPDATE):
        for key, value in (("User", ACCOUNT), ("Group", ACCOUNT), ("FragmentPath", str(UNITS / unit))):
            require(command("systemctl", "show", "-p", key, "--value", unit) == value,
                    f"Unexpected active systemd {key} for {unit}")


def require_nginx_topology():
    piano = NGINX / "sites-enabled/piano.maksonlee.com"
    require(re.search(r"(?m)^\s*listen 8443 ssl http2 proxy_protocol;\s*$", piano.read_text()),
            "Existing shared HTTPS listener differs from the reviewed topology")
    trust = NGINX / "conf.d/realip-proxy.conf"
    checked_metadata(trust, stat.S_ISREG)
    directives = trust.read_text()
    peers = re.findall(r"(?m)^\s*set_real_ip_from ([^\s;]+);\s*$", directives)
    require(re.search(r"(?m)^\s*real_ip_header proxy_protocol;\s*$", directives) and peers and
            all(ipaddress.ip_network(peer, strict=False).prefixlen > 0 for peer in peers),
            "Shared origin real-IP config must trust a restricted set of PROXY-protocol peers")
    loaded = command("nginx", "-T")
    require(f"# configuration file {piano}:" in loaded and f"# configuration file {trust}:" in loaded,
            "Expected origin TLS and trusted-proxy configuration is not loaded")
    named = [line for line in re.findall(r"(?m)^\s*server_name\s+([^;]+);", loaded) if HOST in line.split()]
    enabled = NGINX / "sites-enabled" / HOST
    require(len(named) == (1 if enabled.is_symlink() else 0), "Unexpected duplicate or unscoped Nginx server name")


def prepare_certificate():
    renewal = CERTBOT / "renewal/piano.maksonlee.com.conf"
    checked_metadata(renewal, stat.S_ISREG)
    source = renewal.read_text()

    def setting(name, pattern):
        found = re.findall(rf"(?m)^\s*{name}\s*=\s*({pattern})\s*$", source)
        require(len(found) == 1, f"Existing DNS-01 renewal is missing its reviewed {name} setting")
        return found[0]

    setting("authenticator", "dns-cloudflare")
    account = setting("account", "[0-9a-f]{32}")
    server = setting("server", r"https://acme-v02\.api\.letsencrypt\.org/directory")
    credentials = Path(setting("dns_cloudflare_credentials", r"/[A-Za-z0-9._/-]+"))
    propagation = setting("dns_cloudflare_propagation_seconds", "[1-9][0-9]{0,2}")
    details = credentials.lstat()
    require(stat.S_ISREG(details.st_mode) and details.st_uid in (0, pwd.getpwnam("ubuntu").pw_uid)
            and stat.S_IMODE(details.st_mode) == 0o600,
            "Existing DNS-01 credential file must use the origin's protected owner and mode")
    command("certbot", "certonly", "--non-interactive", "--keep-until-expiring", "--account", account,
            "--server", server, "--cert-name", HOST, "--domains", HOST, "--dns-cloudflare",
            "--dns-cloudflare-credentials", credentials, "--dns-cloudflare-propagation-seconds", propagation, timeout=360)
    certificate = (CERTBOT / "live" / HOST / "fullchain.pem").read_text()
    leaf = re.match(r"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----", certificate)
    require(leaf, "Dedicated certificate is missing")
    return ssl.PEM_cert_to_DER_cert(leaf[0])


def assert_origin_tls(certificate):
    """Exercise the existing Nginx PROXY/TLS listener without waiting for public DNS or SNI routing."""
    context = ssl.create_default_context()
    # A graceful reload signals the master before the new workers can accept TLS.
    deadline = time.monotonic() + 12
    while True:
        remaining = deadline - time.monotonic()
        try:
            with socket.create_connection((LOOPBACK, 8443), timeout=min(8, max(0.1, remaining))) as connection:
                connection.sendall(b"PROXY TCP4 192.0.2.23 127.0.0.1 12345 8443\r\n")
                with context.wrap_socket(connection, server_hostname=HOST) as tls:
                    require(tls.getpeercert(binary_form=True) == certificate,
                            "Origin TLS did not present the dedicated certificate")
            return
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


@contextmanager
def edge_site(vhost):
    require_nginx_topology()
    available = NGINX / "sites-available" / HOST
    enabled = NGINX / "sites-enabled" / HOST
    if os.path.lexists(available):
        checked_metadata(available, stat.S_ISREG, mode=0o644)
        require(available.read_bytes() == vhost, "Existing vhost differs from the reviewed site")
    if os.path.lexists(enabled):
        require(enabled.is_symlink() and enabled.lstat().st_uid == 0 and enabled.resolve() == available,
                "Existing enabled site does not refer to the reviewed dedicated vhost")
    certificate = prepare_certificate()
    created_file = not os.path.lexists(available)
    created_link = not os.path.lexists(enabled)
    reloaded = False
    try:
        if created_file:
            atomic_file(available, vhost, 0o644)
        if created_link:
            enabled.symlink_to(available)
        command("nginx", "-t")
        command("systemctl", "reload", "nginx")
        reloaded = True
        assert_origin_tls(certificate)
        yield hashlib.sha256(certificate).hexdigest()
    except BaseException:
        if created_link:
            enabled.unlink(missing_ok=True)
        if created_file:
            available.unlink(missing_ok=True)
        if reloaded and (created_link or created_file):
            command("nginx", "-t")
            command("systemctl", "reload", "nginx")
        raise


def activate(args, slot, candidate, templates, external=None):
    public = args.operation == "activate-production"
    previous = current_release()
    require(not public or previous is not None, "Commission the service privately before enabling the public route")
    uid, gid = service_account()
    prepare_directory(CONFIG.parent, 0o750, gid=gid)
    prepare_directory(DATA, 0o700, uid=uid, gid=gid)
    prior_config = None
    if os.path.lexists(CONFIG):
        checked_metadata(CONFIG, stat.S_ISREG, mode=0o640, group=gid)
        prior_config = CONFIG.read_bytes()
    require(not previous or prior_config is not None, "Existing release has no reviewed config")
    config, maxmind = config_for_mode(prior_config or templates["commission.toml"].encode(), public)
    user = ("runuser", "-u", ACCOUNT, "--")
    binary = slot / "ip-location"
    require(command(*user, binary, "--version") == f"ip-location {args.version}", "Unexpected executable version on origin")

    created_units = []
    states = {}
    switched = config_changed = restart_attempted = False
    try:
        for name in (WEB, UPDATE, TIMER):
            unit = UNITS / name
            content = templates[name].encode()
            if os.path.lexists(unit):
                checked_metadata(unit, stat.S_ISREG, mode=0o644)
                require(unit.read_bytes() == content, f"Existing {name} differs from the reviewed unit")
            else:
                require(previous is None and command("systemctl", "show", "-p", "LoadState", "--value", name)
                        == "not-found", f"Existing systemd unit conflicts with {name}")
                atomic_file(unit, content, 0o644)
                created_units.append(unit)

        if previous is None:
            # The first updater needs current; the web unit has not started yet.
            switch_release(slot)
            switched = True
            atomic_file(CONFIG, config, 0o640, gid)
            config_changed = True
        if created_units:
            command("systemd-analyze", "verify", *(UNITS / name for name in (WEB, UPDATE, TIMER)))
            command("systemctl", "daemon-reload")

        verify_systemd_identity()
        states = {unit: service_state(unit) for unit in (WEB, TIMER)}
        require(previous is not None or not any(active for _, active in states.values()), "Unexpected running IP Location unit")
        if previous is None:
            command("systemctl", "start", UPDATE, timeout=1220)
        elif not has_local_databases(maxmind):
            bootstrap_empty_slot(binary, states[TIMER][1])
        checks = verify_data(binary, maxmind)

        if previous is not None:
            switch_release(slot)
            switched = True
            if config != prior_config:
                atomic_file(CONFIG, config, 0o640, gid)
                config_changed = True
        restart_attempted = True
        command("systemctl", "restart", WEB, timeout=60)
        port = PUBLIC_PORT if public else PRIVATE_PORT
        assert_http(f"http://{LOOPBACK}:{port}", candidate)
        require(command("systemctl", "is-active", WEB) == "active", "IP Location web unit did not stay active")
        if public:
            external()
        command("systemctl", "enable", WEB)
        command("systemctl", "enable", "--now", TIMER, timeout=60)
    except BaseException:
        try:
            if switched:
                switch_release(previous)
            if config_changed:
                if prior_config is None:
                    CONFIG.unlink()
                else:
                    atomic_file(CONFIG, prior_config, 0o640, gid)
            if restart_attempted:
                for unit, state in states.items():
                    restore_state(unit, state)
                if previous and states[WEB][1]:
                    prior_port, _ = configured_profile(prior_config)
                    local_health(f"http://{prior_port}")
            for path in created_units:
                path.unlink()
            if created_units:
                command("systemctl", "daemon-reload")
        except Exception as error:
            raise RuntimeError(f"Deployment failed and rollback needs review: {error}") from error
        raise

    return {**candidate, "state": "public" if public else "loopback-only", "port": port,
            "previousRelease": previous.name if previous else None, "timerEnabled": True,
            "providerProfile": "dbip-and-maxmind" if maxmind else "dbip-only", "providerChecks": checks}


def origin(args, templates):
    require(os.geteuid() == 0 and platform.machine() == "aarch64", "Origin helper needs root on the approved ARM64 host")
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix="ip-location-release-") as work:
        archive = Path(work) / "bundle.tar.gz"
        remote_input = sys.stdin.buffer
        length = remote_input.readline(12)
        require(re.fullmatch(rb"[1-9][0-9]{0,8}\n", length), "Invalid incoming archive length")
        remaining = int(length)
        require(remaining <= MAX_BYTES, "Oversized input bundle")
        with archive.open("xb") as output:
            while remaining:
                chunk = remote_input.read(min(remaining, 1024 * 1024))
                require(chunk, "Incomplete incoming release archive")
                output.write(chunk)
                remaining -= len(chunk)
        with locked_install():
            slot, candidate = stage(archive, args)
            if args.operation == "activate-production":
                require(current_release(), "Commission the service privately before enabling the public route")
                with edge_site(templates[HOST].encode()) as certificate:
                    def external():
                        print(json.dumps({"awaitingPublicCheck": True, "certificateSha256": certificate}), flush=True)
                        readable, _, _ = select.select([remote_input], [], [], 120)
                        require(readable and remote_input.readline(16) == b"verified\n",
                                "External Jenkins worker did not confirm public verification; restoring the previous service")

                    return {**activate(args, slot, candidate, templates, external), "certificateSha256": certificate}
            receipt = activate(args, slot, candidate, templates)
            # Keep the working private service if the independent origin TLS commissioning needs a retry.
            print("Private DB-IP service checks passed. Preparing the dedicated origin TLS vhost.", file=sys.stderr)
            with edge_site(templates[HOST].encode()) as certificate:
                return {**receipt, "certificateSha256": certificate, "originTlsVerified": True}


def connect_origin(args):
    candidate = verify_worker(args)
    source = Path(__file__).resolve()
    templates = {name: (source.parent / "templates" / name).read_text()
                 for name in (WEB, UPDATE, TIMER, "commission.toml", HOST)}
    program = f"TEMPLATES = {templates!r}\n" + source.read_text()
    remote = shlex.join(["sudo", "-n", "/usr/bin/python3", "-I", "-c", program, "origin", args.operation,
                         args.build, args.commit, args.sha256, args.version])
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12",
           SSH_ORIGIN, remote]
    public_evidence = {}
    with subprocess.Popen(ssh, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        with args.archive.open("rb") as archive:
            process.stdin.write(f"{os.fstat(archive.fileno()).st_size}\n".encode())
            shutil.copyfileobj(archive, process.stdin)
            process.stdin.flush()
        readable, _, _ = select.select([process.stdout], [], [], 1700)
        require(readable, "Origin did not return a deployment response within its time limit")
        first = process.stdout.readline(65536)
        require(first, "Origin did not return a deployment receipt; review the origin diagnostics")
        result = json.loads(first)
        if args.operation == "activate-production":
            certificate = result.get("certificateSha256", "")
            require(result.get("awaitingPublicCheck") is True and HASH.fullmatch(certificate),
                    "Origin did not provide its dedicated certificate for external verification")
            accepted = False
            try:
                public_evidence = assert_edge_tls(certificate)
                assert_http(PUBLIC_URL, candidate, public=True)
                public_evidence["clientIpVerified"] = True
                accepted = True
            finally:
                process.stdin.write(b"verified\n" if accepted else b"rejected\n")
                process.stdin.flush()
            result = json.loads(process.stdout.readline(65536))
        require(process.wait(timeout=60) == 0, "Origin did not finish the approved deployment")
    return {**result, "externalVerification": public_evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("side", choices=("worker", "origin", "edge"))
    parser.add_argument("operation", choices=("verify-only", "commission-origin", "activate-production"))
    parser.add_argument("build")
    parser.add_argument("commit")
    parser.add_argument("sha256")
    parser.add_argument("version")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        require(all(pattern.fullmatch(value) for pattern, value in
                    ((BUILD, args.build), (COMMIT, args.commit), (HASH, args.sha256), (VERSION, args.version))),
                "Provide a release build number, full commit, SHA-256 and version")
        if args.side == "edge":
            require(args.operation == "activate-production", "Edge checks require a public activation")
            endpoints = public_endpoints()
            result = {"hostname": HOST, "publicAddressCount": len(endpoints)}
        elif args.side == "origin":
            require(args.operation != "verify-only" and "TEMPLATES" in globals(), "Use the reviewed deployment runner")
            def interrupted(_number, _frame):
                raise InterruptedError("Deployment process interrupted")

            signal.signal(signal.SIGHUP, interrupted)
            signal.signal(signal.SIGTERM, interrupted)
            result = origin(args, TEMPLATES)
        else:
            require(args.archive is not None, "The exact release archive is required")
            result = verify_worker(args) if args.operation == "verify-only" else connect_origin(args)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, TypeError, KeyError, OSError, tarfile.TarError, subprocess.SubprocessError, RuntimeError) as error:
        # A CalledProcessError from SSH includes the complete reviewed program in its command.
        parser.exit(1, "Origin deployment did not complete; review the origin diagnostics.\n"
                    if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)) else f"{error}\n")


if __name__ == "__main__":
    main()
