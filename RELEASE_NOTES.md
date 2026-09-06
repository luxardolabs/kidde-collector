# Release Notes

Operator-facing release notes live in **[`app/release_notes/`](app/release_notes/)** — one file per release, named to match the [`VERSION`](VERSION) file exactly.

These describe what someone running this collector would actually notice. For the technical record — issue IDs, refactors, deploy steps — see **[CHANGELOG.md](CHANGELOG.md)**.

| Version                                     | Highlights                                                            |
| ------------------------------------------- | --------------------------------------------------------------------- |
| [2026.09.0](app/release_notes/2026.09.0.md) | Smoke/CO/battery on the dashboards, security update, health-check fix |

Format: [`app/release_notes/writing-guide.md`](app/release_notes/writing-guide.md).

> **Surface badges.** This collector is headless — no web UI, so no badge registry to register against, which is the supported headless shape (`luxarch --emit release-notes-guide`). Lines carry plain-text operator-surface labels for the two things an operator actually experiences: `Collector` (the running service) and `Dashboards` (the Grafana dashboards it feeds). Config values and ports stay un-backticked so they are never mistaken for a surface.
