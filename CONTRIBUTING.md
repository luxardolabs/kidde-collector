# Contributing

Thanks for your interest in improving Kidde Collector. Bug reports, fixes, and focused features are all welcome.

## Reporting bugs and requesting features

Open an issue: <https://github.com/luxardolabs/kidde-collector/issues>. For bugs, include the collector logs (redact any credentials), your InfluxDB/Grafana versions, and how to reproduce. For features, describe the use case — this project deliberately stays a focused Kidde → InfluxDB → Grafana collector, so "does one thing well" changes land best.

## Development setup

Dependencies are managed with **Poetry** (`pyproject.toml` + a committed `poetry.lock`); there is no `requirements.txt`.

```bash
git clone https://github.com/luxardolabs/kidde-collector.git
cd kidde-collector
poetry install --with dev     # runtime + dev deps (ruff, mypy, pytest)
```

Run it against the built-in fake Kidde cloud — no account or hardware needed — with `make demo-up` (see [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)).

## Running the checks

**Tests** need only the public dependencies, so anyone can run them:

```bash
poetry run pytest tests       # the unit suite
```

**Style and types** follow one canonical ruff + mypy configuration. You can run the tools locally as a good approximation:

```bash
poetry run ruff check app
poetry run ruff format app
poetry run mypy app
```

The exact fleet configuration (ruff, mypy, pytest, architecture, dependency-CVE, and secret scanning) is enforced through a set of pinned, mount-only guard images that live in a **private registry**. Those images are not needed to contribute — the maintainer runs the full `make check` gate on every change before merge, so don't worry if you can't run the guards yourself. Just keep the code clean (ruff/mypy above) and the tests green.

## Pull requests

1. Fork and branch from `main`.
1. Keep the change focused; add or update tests for behavior changes.
1. Make sure `poetry run pytest tests` passes and the code is ruff/mypy-clean.
1. Open the PR with a clear description of the problem and the fix.

The maintainer runs the fleet guards, may adjust formatting to the canonical config on merge, and will help get a good change over the line.

## License

By contributing, you agree that your contributions are licensed under the project's [AGPL-3.0-only](LICENSE) license.
