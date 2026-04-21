from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from braingraph.cli import main as cli_main
from braingraph.config import BrainGraphConfig
from braingraph.parser.scanner import ProjectScanner


runner = CliRunner()


def test_cli_version_flag_outputs_version() -> None:
    result = runner.invoke(cli_main.app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_cli_watch_supports_seconds_option(tmp_path: Path, monkeypatch) -> None:
    called: dict[str, float | Path | None] = {"project": None, "seconds": None}

    def fake_watch(project_path: Path, duration_seconds: float | None = None) -> None:
        called["project"] = project_path
        called["seconds"] = duration_seconds

    monkeypatch.setattr(cli_main, "watch_project", fake_watch)
    result = runner.invoke(cli_main.app, ["watch", str(tmp_path), "--seconds", "1.5"])
    assert result.exit_code == 0
    assert called["project"] == tmp_path.resolve()
    assert called["seconds"] == 1.5


def test_run_dispatches_init_as_command(tmp_path: Path, monkeypatch) -> None:
    called = {"app": False, "service": False}

    class FakeService:
        def __init__(self, _project: Path) -> None:
            called["service"] = True

        def init_project(self) -> dict[str, str | int]:
            return {"files": 1, "output_dir": "unused"}

    def fake_app() -> None:
        called["app"] = True

    monkeypatch.setattr(cli_main, "BrainGraphService", FakeService)
    monkeypatch.setattr(cli_main, "app", fake_app)
    monkeypatch.setattr(sys, "argv", ["braingraph", "init", str(tmp_path)])
    cli_main.run()
    assert called["app"] is True
    assert called["service"] is False


def test_scanner_ignores_walk_errors(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "app.py"
    src.write_text("def hello():\n    return 'ok'\n", encoding="utf-8")

    def fake_walk(*_args, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror:
            onerror(PermissionError("blocked"))
        yield str(tmp_path), [], ["app.py"]

    monkeypatch.setattr("braingraph.parser.scanner.os.walk", fake_walk)
    config = BrainGraphConfig.for_project(tmp_path)
    files = ProjectScanner(config).iter_files()
    assert files == [src]
