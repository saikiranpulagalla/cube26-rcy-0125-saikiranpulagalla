from __future__ import annotations

import json

from typer.testing import CliRunner

from recovery_manager.cli import app
from recovery_manager.config import get_settings


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
        "evaluate",
        "demo",
    ):
        result = runner.invoke(app, [command, "--help"] if command else ["--help"])
        assert result.exit_code == 0, result.output


def test_evaluate_writes_reproducible_reports(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RECOVERY_DEVELOPMENT_MODE", "true")
    monkeypatch.setenv(
        "RECOVERY_DEV_CREDENTIALS",
        json.dumps({"benchmark-token": {"org_id": "benchmark", "actor_id": "runner", "role": "operator"}}),
    )
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["evaluate", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        result_json = tmp_path / "repair09-results.json"
        assert result_json.exists()
        assert (tmp_path / "repair09-results.md").exists()
        assert json.loads(result_json.read_text(encoding="utf-8"))["engine_benchmarked_revision"]
    finally:
        get_settings.cache_clear()
