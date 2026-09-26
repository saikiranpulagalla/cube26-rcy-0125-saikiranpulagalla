from __future__ import annotations

from typer.testing import CliRunner

from recovery_manager.cli import app


def test_all_headless_commands_render_help() -> None:
    runner = CliRunner()
    for command in (
        "",
        "ingest",
        "status",
        "worker-once",
        "worker-poll",
        "fixture-load",
        "canonicalize-fixture",
        "readiness",
    ):
        result = runner.invoke(app, [command, "--help"] if command else ["--help"])
        assert result.exit_code == 0, result.output
