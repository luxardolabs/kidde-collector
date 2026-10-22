# Change Log

The technical, per-version change log lives in **[`app/change_logs/`](app/change_logs/)** — one file per release, named to match the [`VERSION`](VERSION) file exactly.

This is the internal record: what changed, why, the issue IDs it closed, and any deploy steps. For the operator-facing version see **[RELEASE_NOTES.md](RELEASE_NOTES.md)**.

| Version                                   | Notes                                                                                          |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| [2026.10.3](app/change_logs/2026.10.3.md) | Adversarial audit (9 fixes), health requires a landed write, 1 CRITICAL + 3 HIGH CVEs cleared  |
| [2026.10.2](app/change_logs/2026.10.2.md) | Adversarial-pass fixes: JSON-only startup logs, URL credential redaction, release-recipe gates |
| [2026.10.1](app/change_logs/2026.10.1.md) | multidict CVE fix, fleet JSON logging, scanned multi-arch release, guard catch-up              |
| [2026.10.0](app/change_logs/2026.10.0.md) | Shipped-image CVEs 27 → 0, one compose.yml, commit-identity scrub, guard catch-up              |
| [2026.09.0](app/change_logs/2026.09.0.md) | Guard catch-up, 3 CVEs cleared, 4 fixes, coverage 41% → 99%, Safety & Power dashboards         |

Format: [`app/change_logs/writing-guide.md`](app/change_logs/writing-guide.md). Process: `luxarch --doc FLEET-RELEASE-PROCESS`.
