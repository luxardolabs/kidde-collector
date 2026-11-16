"""The dev host's stack: its own InfluxDB and Grafana, no host port, Grafana on the edge.

FLEET-BUILD-DEPLOY-STANDARD, "The dev host": the dev stack runs where the committed DEV_HOST
says and publishes no host port; its Grafana is served by name through the dev host's edge,
which routes by the Grafana's pinned address on `dev-edge`. These read the committed files
compose reads, so a regression fails here, not at the next deploy.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPOSE = (REPO / "compose.yml").read_text()
DEV_GRAFANA_ENTRYPOINT = REPO / "ops" / "grafana" / "dev-entrypoint.sh"


def _service(name: str) -> str:
    """One service's block: from its `  <name>:` line to the next top-level key or service."""
    match = re.search(
        rf"^  {re.escape(name)}:\n((?:(?:    .*)?\n)+?)(?=^  \S|^\S|\Z)", COMPOSE, re.M
    )
    assert match, f"compose.yml has no service {name}"
    return match.group(1)


@pytest.mark.parametrize("name", ["kidde_dev_influxdb", "kidde_dev_grafana"])
def test_the_dev_pair_publishes_no_host_port(name: str) -> None:
    service = _service(name)
    assert "ports:" not in service, name
    assert 'profiles: ["devhost"]' in service, name


def test_the_dev_grafana_is_served_by_name_on_the_edge() -> None:
    """The edge reaches kidde_dev_grafana:3000 on `dev-edge` by its PINNED address: a Grafana
    off that network, or one that joins without its pin (and so takes a random .128+ address),
    cannot be reached. It stays on the stack's default network to read its InfluxDB."""
    service = _service("kidde_dev_grafana")
    assert "container_name: kidde_dev_grafana" in service
    assert re.search(
        r"^    networks:\n      default: \{\}\n(?:      #.*\n)*      dev-edge:\n"
        r"        ipv4_address: 172\.30\.0\.16$",
        service + "\n",
        re.M,
    )
    assert re.search(r"^  dev-edge:\n    external: true$", COMPOSE, re.M)
    toml = (REPO / ".luxarch.toml").read_text()
    assert re.search(
        r'^\[dev_host\]\nfront = "app"\nservice = "kidde_dev_grafana"$', toml, re.M
    )


def test_each_grafana_reaches_its_own_stacks_influxdb() -> None:
    datasource = (
        REPO / "grafana" / "provisioning" / "datasources" / "influxdb.yml"
    ).read_text()
    assert "url: http://${INFLUX_HOST}:8086" in datasource
    for grafana, influx in (
        ("kidde_dev_grafana", "kidde_dev_influxdb"),
        ("kidde_grafana", "kidde_influxdb"),
    ):
        assert f"INFLUX_HOST: {influx}" in _service(grafana), grafana


def test_the_dev_grafana_starts_through_the_password_guard() -> None:
    service = _service("kidde_dev_grafana")
    # sh exits 0, silently, when its script is a directory: the shape docker gives a missing
    # bind source. Grafana must then fail loudly instead of restarting in silence.
    assert 'entrypoint: ["/bin/sh", "-c", "test -f /ops/dev-entrypoint.sh' in service
    assert "exit 1; }; exec /bin/sh /ops/dev-entrypoint.sh" in service
    assert "- ./ops/grafana/dev-entrypoint.sh:/ops/dev-entrypoint.sh:ro" in service
    # No compose default supplies a password the guard would then have to catch.
    assert "GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-}" in service


def test_a_refused_dev_grafana_fails_the_deploy() -> None:
    """`dev-pin` waits for healthy services; with no healthcheck a Grafana the guard refused
    restarts forever while the deploy reports green."""
    service = _service("kidde_dev_grafana")
    assert re.search(
        r'^    healthcheck:\n      test: \["CMD", "wget", "-q", "-O", "/dev/null", '
        r'"http://localhost:3000/api/health"\]$',
        service,
        re.M,
    )


def test_a_changed_guard_or_provisioning_recreates_the_dev_grafana() -> None:
    """Compose does not recreate a container when a bind-mounted file changes, so a fix to
    the guard would deploy green while the old container keeps running."""
    makefile = (REPO / "Makefile").read_text()
    assert re.search(r"^DEV_RECREATE := kidde_dev_grafana$", makefile, re.M)
    assert re.search(
        r"^kidde_dev_grafana_RECREATE_PATHS := ops/grafana/dev-entrypoint\.sh"
        r" grafana/provisioning$",
        makefile,
        re.M,
    )


def test_the_stacks_admin_passwords_never_reach_the_collector() -> None:
    """The env file the collector loads also configures its stack's InfluxDB and Grafana."""
    collector = _service("kidde-collector")
    assert re.search(
        r'^    environment:\n      INFLUX_PASSWORD: ""\n      GRAFANA_PASSWORD: ""$',
        collector,
        re.M,
    )


def _guard(
    tmp_path: Path, password: str, cli_exit: int = 0, cli_stdout: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the dev Grafana entrypoint with a stand-in `grafana` that records its arguments
    (one call per line) and what it read on stdin (in `<tmp>/stdin`), and prints
    `cli_stdout` on its stdout as the real CLI prints its reasons."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    cli = bin_dir / "grafana"
    cli.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {calls}\ncat > {tmp_path / "stdin"}\n'
        f"printf '%s' '{cli_stdout}'\nexit {cli_exit}\n"
    )
    cli.chmod(0o755)
    run = subprocess.run(
        ["/bin/sh", str(DEV_GRAFANA_ENTRYPOINT)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GF_SECURITY_ADMIN_PASSWORD": password,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return run, (calls.read_text().splitlines() if calls.exists() else [])


@pytest.mark.parametrize(
    "password",
    [
        "",
        "admin",
        "ADMIN",
        "choose-a-password",
        "   ",
        # the CLI reads stdin's first line only: `admin\n…` would set `admin`
        "admin\nZq7-unrelated-suffix",
        "S3cret-pw\r",
    ],
)
def test_the_dev_grafana_refuses_a_default_or_published_password(
    tmp_path: Path, password: str
) -> None:
    """Served by name, the dev Grafana never takes a password anyone can guess: none,
    Grafana's default, or the placeholder this public repo's env template shows."""
    run, calls = _guard(tmp_path, password)
    assert run.returncode == 1
    assert "REFUSING" in run.stderr
    assert calls == []


@pytest.mark.parametrize("password", ["S3cret pw with spaces*", "-Rk9vT2qLm8w"])
def test_the_dev_grafana_sets_its_admin_password_on_every_start(
    tmp_path: Path, password: str
) -> None:
    """Grafana applies GF_SECURITY_ADMIN_PASSWORD only when it creates its database, so a
    volume first started with another password keeps it unless the entrypoint sets it each
    start. It goes on stdin, never as an argument: no `ps` shows it, and a leading `-` is no
    flag."""
    run, calls = _guard(tmp_path, password)
    assert calls == [
        "cli --homepath /usr/share/grafana admin reset-admin-password"
        " --password-from-stdin"
    ]
    assert (tmp_path / "stdin").read_text() == password
    assert password not in run.stdout + run.stderr


def test_the_dev_grafana_does_not_start_when_its_password_cannot_be_set(
    tmp_path: Path,
) -> None:
    """The real CLI prints why it refused (a password policy failure) on stdout: that reason
    reaches the container's log beside the REFUSING line."""
    reason = "Error: the new password does not meet the password policy criteria"
    run, calls = _guard(tmp_path, "S3cret-pw", cli_exit=1, cli_stdout=reason)
    assert run.returncode == 1
    assert "REFUSING" in run.stderr
    assert reason in run.stderr
    assert len(calls) == 1
