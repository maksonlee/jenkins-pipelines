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
   administrative paths, and both address families for public lookup. The origin's
   ModSecurity may refuse a hidden file name such as `/config.toml` with 403 before it
   reaches the app, so the public check accepts 403 or 404 there; the private listener
   must give the app's 404. It compares
   `/api/v1/me` with the same-family address of this QA client measured independently
   over verified HTTPS, with and without a spoofed forwarding header. The client address
   stays in memory. The origin waits for verification from the worker and restores the
   prior service on failure. Retain `deploy-evidence/deployment.json`.

Releases are staged under `/opt/ip-location/releases/<commit>-<release-build>`. The root-owned
`/opt/ip-location/current` link records the active slot. A failed activation restores its
previous link, config and service state. The database stays under `/var/lib/ip-location`.
On later deployments and rollbacks, the candidate verifies the installed data before the
release switch. Eligible local data can be deployed during a provider download outage.
If no database file exists for any enabled provider, the candidate performs a bootstrap
through the existing updater unit and its credentials before verification. A temporary
runtime override selects this executable and the previous timer state is restored after
the bootstrap. If some data exists but fails verification, diagnose it before retrying.
Check `systemctl status ip-location.service ip-location-update.timer` and the updater journal
on the origin for diagnosis. A newly issued valid certificate can stay on disk for a retry.

## Change the installed unit or vhost templates

The deploy job checks root ownership, permissions and the reviewed contents of existing unit
files and the dedicated vhost. When a reviewed template changes, arrange a maintenance window
with the origin operator. Compare the installed file with the previously deployed template and
review local differences before proceeding. Back up only the affected files with ownership and
modes preserved, and record the service and timer state. Install the reviewed replacement as
`root:root` with mode `0644`. Leave the shared Nginx listener and proxy configuration, dedicated
site symlink, service credential drop-ins and the app config in place.

For systemd changes, run `systemd-analyze verify` on the three IP Location units and
`systemctl daemon-reload`, then restart the affected running units or timer as the reviewed
change requires. For Nginx changes, run `nginx -t` before `systemctl reload nginx`. Check the
service, timer, local readiness and public HTTPS, then run the approved deploy with the reviewed
templates. If validation fails, restore the backup and repeat the relevant validation and
reload or restart. Recheck the prior readiness and public HTTPS before ending the window.

## Enable and validate MaxMind

DB-IP-only commissioning provides working lookup with reduced provider redundancy. Once
authorized MaxMind files can be downloaded, install the application repo's existing systemd
credential drop-in using protected, root-owned account ID and key files. Enable
`providers.maxmind` in the root-controlled configuration. Reload units and start the updater.
Once the independent data check passes, restart the web service so it reads the new settings.

```sh
sudo systemctl daemon-reload
sudo systemctl start ip-location-update.service
sudo -u ip-location /opt/ip-location/current/ip-location verify-data \
  --config /etc/ip-location/config.toml --require-all-providers
sudo systemctl restart ip-location.service
```

Check the restarted service's journal for validated MaxMind City and ASN generations. Verify
local readiness and public lookup for IPv4 and IPv6, and exercise provider failover and recovery
with actual authorized data in isolation. Record two-supplier acceptance after those serving
checks pass. Later deploys preserve the enabled provider settings and require both independent
checks if MaxMind is enabled.

## Check the scheduled update

Check `systemctl list-timers ip-location-update.timer` and the updater journal after the next
scheduled trigger. Compare the timer's last-trigger time with the updater's timestamp and exit
status, then run `verify-data` for the enabled providers. A successful deploy or manually started
update does not establish scheduled operation. Investigate an overdue trigger or unsuccessful
update while the serving databases are still eligible.
