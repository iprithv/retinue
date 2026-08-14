# Security Policy

Retinue ships with a security posture from v0.1 (§27.9 of the architecture
spec): private disclosure, a 90-day coordinated-release window, and CVE
requests for confirmed vulnerabilities.

## Reporting a vulnerability

**Do not open a public issue for security reports.**

- Preferred: GitHub → Security → *Report a vulnerability* (private advisory).
- We acknowledge reports within 72 hours and aim to ship a fix within 90 days.
  We'll coordinate the disclosure date with you and credit you in the advisory
  unless you prefer otherwise.

## Scope notes for self-hosted deployments

- `RETINUE_SECRET` (or the generated `~/.retinue/secret` file) protects
  provider keys (AES-256-GCM), the password pepper, and session families.
  Treat it like a database password; rotate it only with a planned re-wrap.
- Provider API keys are encrypted at rest and never returned by the API after
  creation (redacted hints only).
- Refresh tokens rotate on every use; presenting an already-rotated token
  revokes the whole session family and is written to the audit log.
- The image proxy and (future) web tools share one SSRF egress guard that
  denies private, loopback, link-local, and metadata ranges. Report bypasses.
- SQLite deployments: back up with `retinue db backup` (VACUUM INTO), and do
  not place the data dir on a network filesystem.

## Supported versions

Pre-1.0: only the latest released minor receives security fixes.
