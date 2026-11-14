# Egress allow audit

Every request that the egress proxy admits is recorded once per stage in the trusted
store's operator-only event log. Denials remain covered by the existing `BLOCKED` result,
rotation and alert path.

## Schema (`cheeseless.egress.allow.v1`)

One JSON object per event, stored as `kind = "egress_allow"` and the task id as the
event subject:

| Field | Meaning | Bound |
|-------|---------|-------|
| `schema` | Constant `cheeseless.egress.allow.v1` | fixed |
| `request_id` | Stable per-flow id, shared by both stages | 16–64 lowercase hex |
| `stage` | `request_admitted` or `response_clean` | fixed set |
| `host` | Normalised allowlisted host | ≤253, host grammar |
| `method` | Canonical HTTP method (`GET`, `HEAD`, `POST`, `PUT`, `DELETE`, `CONNECT`, `OPTIONS`, `TRACE`, `PATCH`) or `OTHER` | fixed set |
| `decision` | Always `allow` | fixed |
| `rule` | `allowlisted-host`, `model-request` or `registry-get` | fixed set |

There is no field for a URL path, query, fragment, userinfo, header, body, token,
fingerprint or exception text. The event is built from validated values; arbitrary
strings cannot enter it. Request wire text is never copied: a method outside the
canonical set is recorded as `OTHER`, and the rule is one of the policy's closed
allow-rule constants. The domain type re-validates both sets, so a future call site
cannot widen them by passing a different string.

`request_admitted` is written only after the host/method/URL/header/body policy checks
and the request DLP inspection (including archive inspection) have all allowed the
request. `response_clean` is written only when the response stream completed without a
policy denial, DLP hit or scanner failure. A request that is admitted but whose response
is later blocked has exactly one `request_admitted` event.

## Failure policy

The sink is the trusted store, through a `StoreAuditSink` with a lock shared by the proxy
thread. A write failure fails closed:

- Request stage: the proxy returns a catalogue `infra_error`, sends no request upstream,
  and signals the session teardown path with rule `audit` / id `audit-unavailable`.
- Response stage: the response bytes may already have left the proxy (the same accepted
  streaming residual as a mid-stream DLP hit), but the session is torn down immediately
  and no `response_clean` event is claimed.

No exception text, host from an unvalidated source or request content reaches the event,
the client response or the operator alert. `tests/security/test_egress_audit_canary.py`
drives real TLS traffic through the real proxy and checks the positive control, the
blocked path, the denied path and both audit-failure paths for canaries.
