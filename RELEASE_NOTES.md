# Release Notes

Operator-facing release notes live in **[`app/release_notes/`](app/release_notes/)** — one file per release, named to match the [`VERSION`](VERSION) file exactly.

These describe what someone running this collector would actually notice. For the technical record — issue IDs, refactors, deploy steps — see **[CHANGELOG.md](CHANGELOG.md)**.

| Version                                     | Highlights                                                            |
| ------------------------------------------- | --------------------------------------------------------------------- |
| [2026.09.0](app/release_notes/2026.09.0.md) | Smoke/CO/battery on the dashboards, security update, health-check fix |

Format: [`app/release_notes/writing-guide.md`](app/release_notes/writing-guide.md).

> **A note on surface badges.** The writing guide expects badges to be registered in a web app's `templates.py` and releases page. This collector is headless — it has no UI to render them in — so the badges here (`Collector`, `Dashboards`) are plain text naming the two surfaces an operator actually experiences: the running service, and the Grafana dashboards it feeds.
