# Release Notes

Operator-facing release notes live in **[`app/release_notes/`](app/release_notes/)** — one file per release, named to match the [`VERSION`](VERSION) file exactly.

These describe what someone running this collector would actually notice. For the technical record — issue IDs, refactors, deploy steps — see **[CHANGELOG.md](CHANGELOG.md)**.

| Version                                     | Highlights                                                            |
| ------------------------------------------- | --------------------------------------------------------------------- |
| [2026.09.0](app/release_notes/2026.09.0.md) | Smoke/CO/battery on the dashboards, security update, health-check fix |

Format: [`app/release_notes/writing-guide.md`](app/release_notes/writing-guide.md).

> **Surface badges are not used here yet.** The fleet writing guide expects a badge per line, registered in a web app's `templates.py` and releases page. This collector is headless and has neither, so the badge wording for a service with no UI is still to be decided fleet-side. Lines carry no badge until that lands.
