# IP Location origin deployment

`web/ip-location/deploy` uses a successful Linux ARM64 bundle from `web/ip-location/release`.
Supply its number in `RELEASE_BUILD`, full commit in `COMMIT`, checksum in `ARCHIVE_SHA256`
and executable version without the `ip-location` prefix in `VERSION`. The job checks the
artifact's identity and contents, then uses the existing Jenkins agent SSH access to
`ubuntu@app.maksonlee.com`. The agent needs Python 3.11 or newer, `readelf`, a verified SSH
host key and the origin's existing noninteractive sudo access. The trusted origin helper
comes from `jenkins-pipelines`. Systemd and native validation run the release under the
unprivileged `ip-location` account.

1. Select `verify-only` to check the release offline from the origin. Review
   `deploy-evidence/candidate.json` and `pipeline-commit.txt`.
2. Select `commission-origin` with the same inputs and approve installation. The first
   commission uses `/etc/ip-location/config.toml`, port `127.0.0.1:38171`, and
   DB-IP only. The normal updater must succeed, and both DB-IP editions must pass
   independent IPv4 and IPv6 checks before the web unit is started. The service and daily
   updater timer are then enabled. The job prepares a dedicated Certbot DNS-01 certificate
   and vhost on the existing Nginx `8443` PROXY-protocol listener. It validates the origin
   certificate using an explicit local PROXY-protocol probe. The Nginx upstream points to
   port `38170`, which this mode leaves unopened. This step can run before public DNS and
   HAProxy SNI routing. A certificate or vhost failure leaves the validated private service
   running so the same commission can be retried.
3. The edge owner adds the DNS-only `ip` CNAME to `maksonlee.com` and the exact
   `ip.maksonlee.com` SNI match to the existing HAProxy `app` route. Review which peers
   can reach the origin and confirm the existing Nginx real-IP allowlist recognizes
   the actual HAProxy ingress. Reuse the existing `8443` configuration. The job manages
   only the dedicated site, its DNS-01 challenge and certificate. The established Certbot
   timer and deploy hook handle renewal.
4. Select `activate-production` with the reviewed inputs and approve public activation
   after the DNS/HAProxy changes. The service moves to `127.0.0.1:38170` and trusts one
   address supplied only by the local Nginx hop. The Jenkins worker checks the public
   certificate on each published address, matching release files, hidden health and
   administrative paths, and both address families for public lookup. It compares
   `/api/v1/me` with the same-family address of this QA client measured independently
   over verified HTTPS, with and without a spoofed forwarding header. The client address
   stays in memory. The origin waits for verification from the worker and restores the
   prior service on failure. Retain `deploy-evidence/deployment.json`.

Releases are staged under `/opt/ip-location/releases/<commit>-<release-build>`. The root-owned
`/opt/ip-location/current` link records the active slot. A failed activation restores its
previous link, config and service state. The database stays under `/var/lib/ip-location`.
Check `systemctl status ip-location.service ip-location-update.timer` and the updater journal
on the origin for diagnosis. A newly issued valid certificate can stay on disk for a retry.

DB-IP-only commissioning provides working lookup with reduced provider redundancy. Once
authorized MaxMind files can be downloaded, install the application repo's existing systemd
credential drop-in using protected account ID and key files. Enable `providers.maxmind` in the
root-controlled configuration. Run `systemctl daemon-reload`, start `ip-location-update.service`,
then run this independent acceptance check as the service account before recording both suppliers
as verified. Test provider failover and recovery separately in isolation.

```sh
sudo -u ip-location /opt/ip-location/current/ip-location verify-data \
  --config /etc/ip-location/config.toml --require-all-providers
```

Later deploys respect the enabled provider settings and require both independent checks if
MaxMind is enabled. The scheduled updater's first actual timer-run result is a separate
operational check.
