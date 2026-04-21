from __future__ import annotations

import sys
import shutil
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from braingraph import __version__
from braingraph.config import BrainGraphConfig
from braingraph.integrations import INTEGRATIONS, install_integration
from braingraph.service import BrainGraphService
from braingraph.watcher.watch import watch_project

app = typer.Typer(
    help="BrainGraph is a Graphify-style codebase graph memory tool for CLI and AI workflows.",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
console = Console()


def version_callback(value: bool) -> None:
    if not value:
        return
    console.print(__version__)
    raise typer.Exit()


def resolve_project(project: Path | None = None) -> Path:
    return (project or Path.cwd()).resolve()


def ensure_index(project: Path) -> BrainGraphService:
    svc = BrainGraphService(project)
    if not svc.config.db_path.exists():
        svc.init_project()
    return svc


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed BrainGraph version and exit.",
        is_eager=True,
        callback=version_callback,
    ),
) -> None:
    """Run `braingraph .` to scan a repository into braingraph-out."""
    del version
    if ctx.invoked_subcommand is None:
        if not ctx.args:
            console.print(ctx.get_help())
            raise typer.Exit()
        project = resolve_project(Path(ctx.args[0]))
        result = BrainGraphService(project).init_project()
        console.print(
            f"[green]BrainGraph indexed[/green] {result['files']} files into {result['output_dir']}"
        )


@app.command()
def install(project: Path | None = typer.Argument(None, exists=False, dir_okay=True, file_okay=False)) -> None:
    """Create braingraph-out and initial repository memory."""
    path = resolve_project(project)
    result = BrainGraphService(path).init_project()
    console.print(f"[green]Installed[/green] BrainGraph in {result['output_dir']}")


@app.command("init")
def init_command(project: Path | None = typer.Argument(None, exists=False, dir_okay=True, file_okay=False)) -> None:
    """Alias for install for users expecting `braingraph init`."""
    install(project)


@app.command("version")
def version_command() -> None:
    """Show the installed BrainGraph version."""
    console.print(__version__)


@app.command()
def update(project: Path | None = typer.Argument(None, exists=False, dir_okay=True, file_okay=False)) -> None:
    """Refresh graph, summaries, report, and memory."""
    path = resolve_project(project)
    result = BrainGraphService(path).scan()
    console.print(
        f"[green]Updated[/green] {path} -> files={result['files']} chunks={result['chunks']} diagnostics={result['diagnostics']}"
    )


@app.command()
def query(
    text: str,
    project: Path | None = typer.Option(None, "--project", "-p"),
    limit: int = typer.Option(8, min=1, max=30),
) -> None:
    """Query compact context from graph + memory."""
    result = ensure_index(resolve_project(project)).retrieve(text, limit)
    console.print(f"[cyan]Files[/cyan]: {', '.join(result['files']) or 'none'}")
    console.print(f"[cyan]Tokens[/cyan]: raw={result['raw_tokens']} compact={result['context_tokens']}")
    console.print()
    console.print(result["context"] or "No compact context found.")


@app.command()
def explain(
    text: str,
    project: Path | None = typer.Option(None, "--project", "-p"),
    limit: int = typer.Option(12, min=1, max=30),
) -> None:
    """Explain a system using BrainGraph context first."""
    result = ensure_index(resolve_project(project)).explain(text, limit)
    console.print(result["text"])


@app.command(name="path")
def path_command(
    source: str,
    target: str,
    project: Path | None = typer.Option(None, "--project", "-p"),
) -> None:
    """Find the shortest relationship path between modules or symbols."""
    route = ensure_index(resolve_project(project)).shortest_path(source, target)
    console.print(" -> ".join(route))


@app.command()
def stats(project: Path | None = typer.Option(None, "--project", "-p")) -> None:
    """Show scan and token optimization stats."""
    data = ensure_index(resolve_project(project)).stats()
    table = Table(title="BrainGraph Stats")
    table.add_column("Metric")
    table.add_column("Value")
    for key, value in data.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def graph(project: Path | None = typer.Option(None, "--project", "-p")) -> None:
    """Re-export graph.json and graph.html."""
    svc = ensure_index(resolve_project(project))
    svc.graph()
    html = svc.graph_html()
    console.print(f"[green]Exported[/green] graph.json and {html}")


@app.command()
def watch(
    project: Path | None = typer.Argument(None, exists=False, dir_okay=True, file_okay=False),
    seconds: float | None = typer.Option(
        None,
        "--seconds",
        min=0.1,
        help="Stop watching after this many seconds. Useful for smoke tests and CI.",
    ),
) -> None:
    """Watch a project and auto-refresh BrainGraph outputs."""
    path = resolve_project(project)
    console.print(f"[cyan]Watching[/cyan] {path}")
    watch_project(path, duration_seconds=seconds)


@app.command()
def doctor(project: Path | None = typer.Option(None, "--project", "-p")) -> None:
    """Show diagnostics like circular imports and duplicate files."""
    items = ensure_index(resolve_project(project)).diagnostics()
    if not items:
        console.print("[green]No issues found[/green]")
        return
    table = Table(title="BrainGraph Doctor")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("File")
    table.add_column("Message")
    for item in items:
        table.add_row(item["severity"], item["code"], item.get("file_path") or "", item["message"])
    console.print(table)


@app.command()
def clear(
    project: Path | None = typer.Option(None, "--project", "-p"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
) -> None:
    """Remove braingraph-out from a project."""
    path = BrainGraphConfig.load(resolve_project(project)).output_dir
    if not path.exists():
        console.print("[yellow]No braingraph-out directory found[/yellow]")
        raise typer.Exit()
    if not yes and not typer.confirm(f"Delete {path}?"):
        raise typer.Abort()
    last_error: OSError | None = None
    for _attempt in range(3):
        try:
            shutil.rmtree(path)
            break
        except FileNotFoundError:
            break
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    if path.exists():
        if last_error is not None:
            raise last_error
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted[/green] {path}")


def _install_tool(tool: str, project: Path | None) -> None:
    repo_file = install_integration(resolve_project(project), tool)
    label = str(INTEGRATIONS[tool].get("label", tool))
    console.print(f"[green]{label} integration installed[/green] -> {repo_file}")


for tool_name, spec in INTEGRATIONS.items():
    label = str(spec.get("label", tool_name))
    subapp = typer.Typer(help=f"{label} integration commands.")

    @subapp.command("install")
    def install_tool(
        project: Path | None = typer.Argument(None, exists=False, dir_okay=True, file_okay=False),
        _tool: str = tool_name,
    ) -> None:
        _install_tool(_tool, project)

    for command_name in tuple(spec.get("commands", (tool_name,))):
        app.add_typer(subapp, name=command_name)


def run() -> None:
    commands = {
        "install",
        "init",
        "update",
        "query",
        "path",
        "explain",
        "version",
        "stats",
        "graph",
        "watch",
        "doctor",
        "clear",
        "--help",
        "-h",
        "--version",
    }
    for spec in INTEGRATIONS.values():
        commands.update(str(command) for command in tuple(spec.get("commands", ())))
    argv = sys.argv[1:]
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        result = BrainGraphService(resolve_project(Path(argv[0]))).init_project()
        console.print(
            f"[green]BrainGraph indexed[/green] {result['files']} files into {result['output_dir']}"
        )
        return
    app()


if __name__ == "__main__":
    run()
