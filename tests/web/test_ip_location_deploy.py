"""Offline regression coverage for the IP Location deployment transaction."""

import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit


PROJECT = Path(__file__).resolve().parents[2] / "Jenkinsfile/web/ip-location/deploy"
SPEC = importlib.util.spec_from_file_location("ip_location_release_ops", PROJECT / "release_ops.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.profile = (PROJECT / "templates/commission.toml").read_bytes()
        self.identity = SimpleNamespace(build="23", commit="d" * 40, version="0.1.0", sha256=None)

    def bundle(self, kind="jenkins-release", version="0.1.0", unsafe=False):
        bundle = self.root / "bundle.tar.gz"
        files = {"ip-location": b"binary", "web/index.html": b"index", "web/assets/script-12345678.js": b"javascript"}
        meta = {"schema_version": 1, "kind": kind, "target": "aarch64-unknown-linux-musl", "commit": self.identity.commit,
                "jenkins_build": self.identity.build, "binary_version": f"ip-location {version}",
                "source_sha256": "a" * 64, "lockfiles": {"Cargo.lock": "b" * 64, "web/pnpm-lock.yaml": "c" * 64},
                "sha256": {name: ops.hashlib.sha256(content).hexdigest() for name, content in files.items()}}
        files["release.json"] = json.dumps(meta).encode()
        if unsafe:
            files["../ip-location"] = b"unsafe"
        with tarfile.open(bundle, "w:gz", format=tarfile.USTAR_FORMAT) as package:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o755 if name == "ip-location" else 0o644
                package.addfile(member, io.BytesIO(content))
        self.identity.sha256 = ops.digest(bundle)
        return bundle

    def test_bundle_pins_exact_artifact_and_metadata(self):
        archive = self.bundle()
        result = ops.verify_bundle(self.identity, archive, self.root / "valid")
        self.assertEqual((result["releaseBuild"], result["hostname"], result["version"]), ("23", "ip.maksonlee.com", "0.1.0"))
        self.identity.sha256 = "1" * 64
        with self.assertRaisesRegex(ValueError, "approved SHA-256"):
            ops.verify_bundle(self.identity, archive, self.root / "tampered")
        for options, error in (({"kind": "local-development"}, "Release metadata"),
                               ({"version": "1.2.3"}, "Release metadata"), ({"unsafe": True}, "Unexpected archive path")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, error):
                ops.verify_bundle(self.identity, self.bundle(**options), self.root / ("reject-" + next(iter(options))))
        self.assertFalse((self.root / "ip-location").exists())

    def test_commissioning_does_not_open_the_planned_public_upstream(self):
        commissioned, maxmind = ops.config_for_mode(self.profile, False)
        self.assertEqual(commissioned, self.profile)
        self.assertFalse(maxmind)
        self.assertEqual(ops.configured_profile(commissioned)[0], "127.0.0.1:38171")
        public, maxmind = ops.config_for_mode(commissioned, True)
        self.assertFalse(maxmind)
        self.assertIn(b'listen = "127.0.0.1:38170"', public)
        self.assertIn(b"trust_x_forwarded_for = true", public)
        self.assertIn(b'trusted_proxy_cidrs = ["127.0.0.1/32"]', public)
        from_crlf, _ = ops.config_for_mode(commissioned.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"), True)
        self.assertEqual(public, from_crlf)
        with self.assertRaisesRegex(ValueError, "move a public service back"):
            ops.config_for_mode(public, False)

    def test_direct_provider_checks_require_both_editions_and_both_families(self):
        verified = {"complete": True, "verification": [
            {"provider": "dbip", "capability": capability, "status": "passed", "ipv4_sample": "1.1.1.1",
             "ipv6_sample": "2606:4700:4700::1111"} for capability in ("location", "network")
        ]}
        with patch.object(ops, "command", return_value=json.dumps(verified)) as command:
            self.assertEqual(len(ops.verify_data("ip-location", False)), 2)
            self.assertNotIn("--require-all-providers", command.call_args.args)
            with self.assertRaisesRegex(ValueError, "City/ASN IPv4/IPv6"):
                ops.verify_data("ip-location", True)
            self.assertIn("--require-all-providers", command.call_args.args)
        verified["verification"][1]["ipv6_sample"] = "2.2.2.2"
        with patch.object(ops, "command", return_value=json.dumps(verified)), \
                self.assertRaisesRegex(ValueError, "City/ASN IPv4/IPv6"):
            ops.verify_data("ip-location", False)

    def test_first_install_rollback_keeps_data_and_removes_its_new_units(self):
        self.identity.operation = "commission-origin"
        templates = {path.name: path.read_text() for path in (PROJECT / "templates").iterdir()}
        units, config, data, slot = (self.root / name for name in ("units", "config.toml", "data", "candidate"))
        units.mkdir()
        data.mkdir()
        (data / "published-current").write_text("preserve")
        updater_started = []

        def command(*args, **kwargs):
            if args == ("systemctl", "start", ops.UPDATE):
                self.assertEqual(switch.call_args.args, (slot,))
                self.assertTrue(config.is_file())
                updater_started.append(True)
            return {"runuser": "ip-location 0.1.0", "systemd-analyze": ""}.get(args[0], "not-found")

        def atomic_file(path, content, *args):
            path.write_bytes(content)

        with patch.multiple(ops, UNITS=units, CONFIG=config, DATA=data), \
                patch.object(ops, "current_release", return_value=None), \
                patch.object(ops, "service_account", return_value=(1000, 1000)), \
                patch.object(ops, "prepare_directory"), patch.object(ops, "command", side_effect=command), \
                patch.object(ops, "atomic_file", side_effect=atomic_file), patch.object(ops, "switch_release") as switch, \
                patch.object(ops, "verify_systemd_identity"), \
                patch.object(ops, "service_state", return_value=(False, False)), \
                patch.object(ops, "configured_profile", return_value=("127.0.0.1:38171", False)), \
                patch.object(ops, "verify_data", side_effect=ValueError("City missing")), \
                self.assertRaisesRegex(ValueError, "City missing"):
            ops.activate(self.identity, slot, {}, templates)
        self.assertEqual([call.args[0] for call in switch.call_args_list], [slot, None])
        self.assertEqual(updater_started, [True])
        self.assertFalse(config.exists())
        self.assertEqual(list(units.iterdir()), [])
        self.assertEqual((data / "published-current").read_text(), "preserve")

    def test_public_probe_failure_restores_previous_release_and_private_listener(self):
        self.identity.operation = "activate-production"
        previous = self.root / "previous"
        slot = self.root / "candidate"
        units, config = self.root / "units", self.root / "config.toml"
        units.mkdir()
        profile = self.profile.replace(b'/var/lib/ip-location', str(self.root).encode())
        config.write_bytes(profile)
        templates = {path.name: path.read_text() for path in (PROJECT / "templates").iterdir()}
        for name in (ops.WEB, ops.UPDATE, ops.TIMER):
            (units / name).write_bytes(templates[name].encode())
        database = self.root / "providers" / "dbip" / "location" / ("a" * 64) / "database.mmdb"
        database.parent.mkdir(parents=True)
        database.touch()

        def atomic_file(path, content, *args):
            path.write_bytes(content)

        def command(*args, **kwargs):
            if args == ("systemctl", "start", ops.UPDATE):
                self.fail("Existing data should allow activation without a provider download")
            return "ip-location 0.1.0" if args[0] == "runuser" else "active"

        def verify_data(binary, maxmind):
            self.assertEqual(binary, slot / "ip-location")
            self.assertFalse(maxmind)
            switch.assert_not_called()
            return []

        def fail_public():
            self.assertIn(b'listen = "127.0.0.1:38170"', config.read_bytes())
            raise ValueError("client IP mismatch")

        with patch.multiple(ops, UNITS=units, CONFIG=config, DATA=self.root), patch.object(ops, "checked_metadata"), \
                patch.object(ops, "current_release", return_value=previous), \
                patch.object(ops, "service_account", return_value=(1000, 1000)), \
                patch.object(ops, "prepare_directory"), patch.object(ops, "command", side_effect=command), \
                patch.object(ops, "atomic_file", side_effect=atomic_file), patch.object(ops, "switch_release") as switch, \
                patch.object(ops, "verify_systemd_identity"), \
                patch.object(ops, "service_state", return_value=(True, True)), patch.object(ops, "restore_state"), \
                patch.object(ops, "assert_http"), patch.object(ops, "local_health") as health, \
                patch.object(ops, "verify_data", side_effect=verify_data), self.assertRaisesRegex(ValueError, "client IP mismatch"):
            ops.activate(self.identity, slot, {}, templates, fail_public)
        self.assertEqual([call.args[0] for call in switch.call_args_list], [slot, previous])
        self.assertEqual(config.read_bytes(), profile)
        health.assert_called_once_with("http://127.0.0.1:38171")

    def test_empty_slot_bootstrap_uses_candidate_and_restores_timer_after_updater_failure(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        override = runtime / f"{ops.UPDATE}.d" / "90-ip-location-bootstrap.conf"
        candidate = self.root / "candidate" / "ip-location"
        lifecycle = []

        def command(*args, **kwargs):
            lifecycle.append(args)
            if args == ("systemctl", "show", "-p", "ActiveState", "--value", ops.UPDATE):
                return "inactive"
            if args == ("systemctl", "show", "-p", "ExecStart", "--value", ops.UPDATE):
                invocation = f"{candidate} update-data --config {ops.CONFIG}"
                self.assertEqual(override.read_text(), f"[Service]\nExecStart=\nExecStart={invocation}\n")
                return f"{{ argv[]={invocation} ; ignore_errors=no }}"
            if args == ("systemctl", "start", ops.UPDATE):
                raise ValueError("updater returned failure")
            return ""

        def prepare_directory(path, mode):
            path.mkdir(mode=mode)

        def atomic_file(path, content, *args):
            path.write_bytes(content)

        with patch.object(ops, "RUNTIME_UNITS", runtime), patch.object(ops, "command", side_effect=command), \
                patch.object(ops, "prepare_directory", side_effect=prepare_directory), \
                patch.object(ops, "atomic_file", side_effect=atomic_file), \
                self.assertRaisesRegex(ValueError, "updater returned failure"):
            ops.bootstrap_empty_slot(candidate, timer_active=True)

        self.assertFalse(override.exists())
        self.assertEqual(lifecycle[0], ("systemctl", "stop", ops.TIMER))
        self.assertEqual(lifecycle[-2:], [("systemctl", "daemon-reload"), ("systemctl", "start", ops.TIMER)])

    def test_deploy_job_checks_release_and_approval_before_origin_access(self):
        pipeline = (PROJECT / "Jenkinsfile").read_text()
        before, after = pipeline.split("stage('Apply approved changes and verify')")
        self.assertNotIn("githubPush()", pipeline)
        self.assertIn("disableRestartFromStage()", before)
        self.assertIn("release.result != 'SUCCESS'", before)
        self.assertIn("worker verify-only", before)
        self.assertIn("input message:", before)
        self.assertIn('worker "$IPLOC_MODE"', after)

    def test_origin_tls_waits_for_new_nginx_worker_with_matching_sni(self):
        tls = MagicMock()
        tls.getpeercert.return_value = b"dedicated certificate"
        context = MagicMock()
        context.wrap_socket.side_effect = [ops.ssl.SSLError("unrecognized name"), tls]
        tls.__enter__.return_value = tls
        with patch.object(ops.ssl, "create_default_context", return_value=context), \
                patch.object(ops.socket, "create_connection", return_value=MagicMock()), \
                patch.object(ops.time, "monotonic", side_effect=(0, 0, 0, 1)), \
                patch.object(ops.time, "sleep") as sleep:
            ops.assert_origin_tls(b"dedicated certificate")
        self.assertEqual(context.wrap_socket.call_count, 2)
        self.assertEqual(context.wrap_socket.call_args.kwargs, {"server_hostname": "ip.maksonlee.com"})
        sleep.assert_called_once()

    def test_public_hidden_files_may_be_refused_by_the_waf_but_the_private_listener_needs_404(self):
        index = b'<script type="module" src="/assets/script-12345678.js"></script>'
        script = b"javascript"
        candidate = {"files": {"web/index.html": ops.hashlib.sha256(index).hexdigest(),
                               "web/assets/script-12345678.js": ops.hashlib.sha256(script).hexdigest()}}
        client = "8.8.4.4"

        class Response(io.BytesIO):
            def __init__(self, status, body=b""):
                super().__init__(body)
                self.status = status

        def site(config_status):
            def respond(request, timeout):
                url = urlsplit(request.full_url)
                public = url.hostname == ops.HOST
                if url.path in ("/health", "/health/live", "/health/ready"):
                    return Response(404 if public else 200)
                if url.path == "/config.toml":
                    return Response(config_status)
                if url.path in ("/api/v1/deploy-probe-unknown", "/release.json", "/ip-location"):
                    return Response(404)
                if url.path == "/api/v1/lookup":
                    ip = parse_qs(url.query)["ip"][0]
                    component = {"status": "ok", "source": {"provider": "dbip"}}
                    return Response(200, json.dumps({
                        "status": "ok", "ip": ip, "ip_version": ops.ipaddress.ip_address(ip).version,
                        "location": component, "network": component}).encode())
                bodies = {"/": index, "/assets/script-12345678.js": script,
                          "/api/v1/config": b'{"schema_version": "1.0"}', "/api/v1/me": json.dumps({"ip": client}).encode()}
                return Response(200, bodies[url.path])
            return MagicMock(open=respond)

        private = f"http://{ops.LOOPBACK}:{ops.PRIVATE_PORT}"
        with patch.object(ops, "external_client_ip", return_value=ops.ipaddress.ip_address(client)):
            with patch.object(ops, "build_opener", return_value=site(403)):
                ops.assert_http(ops.PUBLIC_URL, candidate, public=True)
                with self.assertRaisesRegex(ValueError, f"Unexpected HTTP 403 for {private}/config.toml"):
                    ops.assert_http(private, candidate)
            with patch.object(ops, "build_opener", return_value=site(404)):
                ops.assert_http(ops.PUBLIC_URL, candidate, public=True)
                ops.assert_http(private, candidate)
            with patch.object(ops, "build_opener", return_value=site(200)), \
                    self.assertRaisesRegex(ValueError, f"Unexpected HTTP 200 for {ops.PUBLIC_URL}/config.toml"):
                ops.assert_http(ops.PUBLIC_URL, candidate, public=True)

    def test_origin_tls_stops_if_reloaded_workers_never_serve_candidate(self):
        context = MagicMock()
        context.wrap_socket.side_effect = ops.ssl.SSLError("unrecognized name")
        with patch.object(ops.ssl, "create_default_context", return_value=context), \
                patch.object(ops.socket, "create_connection", return_value=MagicMock()), \
                patch.object(ops.time, "monotonic", side_effect=(0, 0, 12)), \
                self.assertRaisesRegex(ops.ssl.SSLError, "unrecognized name"):
            ops.assert_origin_tls(b"dedicated certificate")


if __name__ == "__main__":
    unittest.main()
