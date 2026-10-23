# zCloud dashboard — authenticated HTTPS cutover on new VPS

Owner scope: dashboard publication only. Canonical umbrella: #1588.

New target: VM-7eb5ee16-2c71-440d-b7fd-8095d204e930 (95.211.40.88).
Keep vps-bb300bba as rollback. This runbook does not start the scheduler,
merge SQLite, start Firefox or register GitHub runners.

## Why this exists

The legacy dashboard access recovery workflow still probes the old
198.244.191.182:8765 HTTP route. Its privileged recovery job is quarantined.
Directly opening port 8765 on the new host would expose the control-plane API.
Use loopback-only backend binding and authenticated HTTPS Caddy instead.

## Preconditions

1. The old zCloud writer/scheduler/browser leases are fenced. The final
   consistent SQLite backup has been reconciled under PR #1583 ownership.
   Never start a second production writer.
2. The new zennay-cloud.service is already live on 127.0.0.1:8765 only.
   Its local /api/status returns a timezone-aware timestamp no more than 5 minutes old (and no more than 2 minutes in the future) plus a projects array.
3. The DNS owner must independently confirm the authoritative delegation
   and the intended address records before publication. The publisher's
   getent ahosts check is a resolver-view preflight, not authoritative DNS
   proof; it requires the complete resolver address set to contain only
   95.211.40.88 and refuses any IPv6/AAAA result because this target has no
   approved IPv6 endpoint. `--apply` additionally requires the separate
   `ZCLOUD_DNS_CUTOVER_CONFIRMED=yes` attestation. Remove an unintended
   AAAA record or add a separately reviewed IPv6-aware implementation before
   publication.
   The zSSH repository's last observed authoritative nameservers for
   zennay.nl are ns1.site.eu, ns2.site.nl, ns3.site.be and ns4.site.de,
   **not Cloudflare**. Confirm current delegation with the DNS owner before
   publication, update only the zcloud subdomain at its real provider, and
   preserve apex and zssh records. Do not dispatch the historical Cloudflare
   DNS publisher for a zone that Cloudflare does not host.
4. The Caddy owner has approved an exact root Caddyfile import:

   ~~~caddyfile
   import /etc/caddy/sites-enabled/*
   ~~~

   Do not replace the existing zSSH site, Caddyfile or another site's files.
5. The operator securely provisions /etc/caddy/zcloud-dashboard-auth.caddy,
   root:caddy mode 0640, with one bcrypt-auth block:

   ~~~caddyfile
   basic_auth {
       YOUR_USERNAME YOUR_BCRYPT_HASH
   }
   ~~~

   Replace those placeholders using a protected credential path. Do not
   publish a password or hash in chat, GitHub, Notion, CI or command arguments.
   Authentication must cover all paths, including /api/*.

## Operator run (new host only, after approved code release)

~~~bash
cd /home/ubuntu/zennay-cloud
bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --status
# Only when old writers are fenced and the final DB handoff is complete:
sudo env ZCLOUD_DNS_CUTOVER_CONFIRMED=yes \
  ZCLOUD_SQLITE_CUTOVER_CONFIRMED=yes \
  bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --apply
# After approval and actual publication, independently confirm logged-in use:
sudo env ZCLOUD_DASHBOARD_NETRC=/root/.zcloud-dashboard-verify.netrc \
  bash ops/publish-zcloud-dashboard-new-vps-20261022.sh --verify
~~~

### Authenticated application verification (read-only)

`--verify` does **not** start or restart services or write to Caddy, SQLite,
GitHub runners, the browser, or the dashboard. A privileged operator must
securely prepare `/root/.zcloud-dashboard-verify.netrc` with the approved
dashboard credentials using a private mechanism (not a command-line password,
GitHub, Notion or CI). The file must be a regular, root-owned, root-group,
mode-0600 file, non-symlink, with at most 4 KiB content. Never include its
contents in logs or receipts; remove it after the operator verification
window if no longer needed.

The read-only verifier requires a healthy loopback backend, matching resolver
DNS, anonymous HTTP 401 for the homepage and critical API endpoints, a valid
authenticated zCloud HTML page and an authenticated `/api/status` JSON
response with timezone-aware freshness (at most five minutes old and at most
two minutes in the future). It also checks that authenticated
`/api/portfolio-queue` returns the live SQLite queue read-model and
`/api/runner-targets` returns the runner map with a positive global limit.
The authenticated read-models are never used to claim ownership, dispatch
jobs, reconfigure capacity or write database rows. HTML and JSON are
size- and time-bounded and never printed; only one constant success indicator
is emitted. This does not grant deployment rights or certify external
network reachability.

The script refuses extra arguments, a wrong host, unhealthy or non-loopback
API, missing cutover/DNS-owner attestations, IPv4/IPv6 DNS drift, invalid
auth, a missing Caddy import, or a Caddyfile/import directory that is not
root-owned and non-group/world-writable, as well as an unexpected existing
site. It stages one drop-in, validates Caddy, reloads
and checks that anonymous HTTPS returns HTTP 401 for the homepage and all three
critical API entry points: `/api/status`, `/api/portfolio-queue` and
`/api/runner-targets`. A homepage-only 401 is explicitly insufficient. Rollback quarantines only
the exact site inode created by that invocation into a root-owned 0700
quarantine outside the entire Caddy configuration tree. The move is allowed
only on the same filesystem, so it remains atomic and cannot be picked up by
a broad Caddy import.
If a rollback reload fails, the script reports the live state as unverified
instead of claiming successful recovery. An existing site returning HTTP 401
is not treated as a successful publication.

The operator must independently confirm from another network that:
- TLS certificate and hostname are correct;
- anonymous dashboard and critical API calls are denied, including
  `/api/status`, `/api/portfolio-queue` and `/api/runner-targets`;
- authorized login returns actual dashboard HTML and live API status;
- /api/portfolio-queue and /api/runner-targets reflect the new VPS;
- port 8765 is not directly reachable from the internet;
- no old/new scheduler duplicate writes or worker leases exist.

HTTP 401 on the four unauthenticated probes is an auth-boundary check, not
proof that an authorized user can see the dashboard. The optional `--verify`
mode checks authenticated page and API behavior without echoing credentials;
independent external-network confirmation remains required.
Do not mark public-live until an authenticated application response is shown.

## Release truth

This is currently source preparation only. On 23 October 2026 the
connected private zSSH 0.1.2 profile reported `exec_mode=full` with an
inspect/restart capability for `zennay-cloud.service`. The read-only status
inspection found the service running but still logging a missing explicit
`browseragent` runtime contract. Those technical capabilities are **not**
approval to restart the service, run this publisher, edit Caddy, mutate
SQLite or claim the final old/new writer cutover. Its Caddy systemd grant
was not observed in the dedicated service capability list.
Production publication still requires authorized DNS/Caddy ownership,
current exact-head review/CI, final writer-fence receipts and a deliberate
operator release. Do not use unrestricted shell access as a substitute
for those gates.
