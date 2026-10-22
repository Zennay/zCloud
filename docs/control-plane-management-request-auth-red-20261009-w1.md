# zCloud management-request origin claims: offline red contract (9 October 2026)

## Observed source behavior (not a live exploit claim)

The current `server.py:action_request_allowed(handler)` trusts loopback and explicitly allowlisted client addresses. For a different remote client, the predicate also returns true when the request self-reports a matching `Host` with `Origin` or `Referer`, and claims `Sec-Fetch-Site: same-origin` or `same-site`. A non-browser HTTP client can choose all those header values. They are useful browser CSRF hints, **not authentication of the socket peer**.

This source-level behavior alone does **not** establish that any deployed endpoint is internet-reachable or that a later endpoint-specific token/permission check can be bypassed. It does establish that this predicate by itself is insufficient to grant remote write authority.

## Regression carrier

`tests/test_management_header_auth_red_20261009_w1.py` parses only this one function from `server.py` and executes it with synthetic handler objects and a temporary allowlist. It never imports the server module or starts a socket, and it never reads live `history.db`, secrets, worker state, queue data, browser state or VPS state.

Its negative tests require unknown remote clients with forged same-origin and same-site request headers to be denied. **Those tests deliberately fail with the current predicate**, while explicit IP-allowlist, loopback and untrusted-empty-header compatibility cases remain green. Do not relax the red assertions to make CI green.

## Owner-gated remediation

- Serialized `server.py` owner: #580 / PWQ-41; deployment/integration owner: #1089. This PR must not edit those paths or mutate production.
- First inventory the **full call chain** of management routes and external ingress/proxy protections. If a separate authenticated boundary already exists, document and test the composite boundary; avoid inventing a vulnerability claim.
- If a writable remote route relies solely on the predicate, require verifiable authentication/authorization (for example an established bounded management credential). Never substitute `Origin`, `Referer`, `Host`, `Sec-Fetch-Site` or an externally supplied `X-Forwarded-For` for identity.
- Preserve intended explicit trusted-IP/loopback operations only after checking the established network model; apply CSRF protections separately when browser credentials are involved.
- Prove remote forged-header rejection, actual loopback and trusted-list compatibility, and token/permission failure tests; then exact-head full regression and permanent-VPS read-only acceptance. Deploy only through authorized serialized gates.

No network exploitation, authenticated action, live work dispatch, runner restart, repository branch-protection change or deployment was performed in this test carrier.
