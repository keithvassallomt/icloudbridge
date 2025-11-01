"""Command-line interface for iCloudBridge."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from icloudbridge import __version__
from icloudbridge.core.config import load_config
from icloudbridge.core.sync import NotesSyncEngine

# Create Typer app
app = typer.Typer(
    name="icloudbridge",
    help="Synchronize Apple Notes & Reminders to NextCloud, CalDAV, and local folders",
    add_completion=False,
)

# Create console for rich output
console = Console()


def setup_logging(log_level: str) -> None:
    """Configure logging with rich handler."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@app.callback()
def main(
    ctx: typer.Context,
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file",
        exists=True,
        dir_okay=False,
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        "-l",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    ),
) -> None:
    """iCloudBridge - Sync Apple Notes & Reminders."""
    # Store config in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_file)

    # Set up logging based on config or CLI arg
    if log_level == "INFO" and ctx.obj["config"].general.log_level != "INFO":
        log_level = ctx.obj["config"].general.log_level
    setup_logging(log_level)


@app.command()
def version() -> None:
    """Show version information."""
    import platform

    table = Table(title="iCloudBridge Version Information")
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Version", __version__)
    table.add_row("Python", platform.python_version())
    table.add_row("Platform", platform.platform())
    table.add_row("Architecture", platform.machine())

    console.print(table)


@app.command()
def config(
    ctx: typer.Context,
    show: bool = typer.Option(
        False,
        "--show",
        "-s",
        help="Show current configuration",
    ),
) -> None:
    """Manage configuration."""
    cfg = ctx.obj["config"]

    if show:
        table = Table(title="iCloudBridge Configuration")
        table.add_column("Setting", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        # General settings
        table.add_row("Data Directory", str(cfg.general.data_dir))
        table.add_row("Config File", str(cfg.general.config_file or "Not set"))
        table.add_row("Log Level", cfg.general.log_level)

        # Notes settings
        table.add_row("", "")  # Separator
        table.add_row("[bold]Notes[/bold]", "")
        table.add_row("Enabled", "✓" if cfg.notes.enabled else "✗")
        table.add_row(
            "Remote Folder",
            str(cfg.notes.remote_folder) if cfg.notes.remote_folder else "Not set",
        )

        # Reminders settings
        table.add_row("", "")  # Separator
        table.add_row("[bold]Reminders[/bold]", "")
        table.add_row("Enabled", "✓" if cfg.reminders.enabled else "✗")
        table.add_row(
            "CalDAV URL",
            cfg.reminders.caldav_url if cfg.reminders.caldav_url else "Not set",
        )
        table.add_row(
            "CalDAV Username",
            cfg.reminders.caldav_username if cfg.reminders.caldav_username else "Not set",
        )

        console.print(table)
    else:
        console.print(
            f"[yellow]Configuration file:[/yellow] {cfg.general.config_file or 'Not set'}"
        )
        console.print(
            f"[yellow]Data directory:[/yellow] {cfg.general.data_dir}",
        )
        console.print(
            "\n[dim]Use --show to display full configuration[/dim]",
        )


@app.command()
def health(ctx: typer.Context) -> None:
    """Check application health and dependencies."""
    cfg = ctx.obj["config"]

    console.print("[bold]Health Check[/bold]\n")

    # Check data directory
    if cfg.general.data_dir.exists():
        console.print("✓ Data directory exists", style="green")
    else:
        console.print("✗ Data directory does not exist", style="red")

    # Check database
    if cfg.db_path.exists():
        console.print("✓ Database exists", style="green")
    else:
        console.print("ℹ Database not initialized", style="yellow")

    # Check notes remote folder
    if cfg.notes.enabled:
        if cfg.notes.remote_folder and cfg.notes.remote_folder.exists():
            console.print("✓ Notes remote folder exists", style="green")
        elif cfg.notes.remote_folder:
            console.print("✗ Notes remote folder does not exist", style="red")
        else:
            console.print("ℹ Notes remote folder not configured", style="yellow")

    # Check reminders CalDAV
    if cfg.reminders.enabled:
        if cfg.reminders.caldav_url:
            console.print("✓ CalDAV URL configured", style="green")
        else:
            console.print("ℹ CalDAV URL not configured", style="yellow")

    console.print("\n[dim]Status: Ready[/dim]")


# Notes subcommand group
notes_app = typer.Typer(help="Manage notes synchronization")
app.add_typer(notes_app, name="notes")


@notes_app.command("sync")
def notes_sync(
    ctx: typer.Context,
    folder: Optional[str] = typer.Option(
        None,
        "--folder",
        "-f",
        help="Specific folder to sync (syncs all if not specified)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview changes without applying them",
    ),
) -> None:
    """Synchronize notes between Apple Notes and markdown files."""
    cfg = ctx.obj["config"]

    # Check if notes sync is enabled
    if not cfg.notes.enabled:
        console.print("[red]Notes sync is not enabled in configuration[/red]")
        console.print("[dim]Enable it in your config file or use environment variables[/dim]")
        raise typer.Exit(1)

    # Check if remote folder is configured
    if not cfg.notes.remote_folder:
        console.print("[red]Notes remote folder is not configured[/red]")
        console.print("[dim]Set ICLOUDBRIDGE_NOTES_REMOTE_FOLDER in your config[/dim]")
        raise typer.Exit(1)

    if dry_run:
        console.print("[yellow]Dry run mode is not yet implemented[/yellow]")
        console.print("[dim]For now, running actual sync...[/dim]\n")

    async def run_sync():
        # Initialize sync engine
        sync_engine = NotesSyncEngine(
            markdown_base_path=cfg.notes.remote_folder,
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        # Get folders to sync
        if folder:
            folders_to_sync = [folder]
            console.print(f"[cyan]Syncing folder:[/cyan] {folder}\n")
        else:
            console.print("[cyan]Fetching folders from Apple Notes...[/cyan]")
            all_folders = await sync_engine.list_folders()
            folders_to_sync = [f["name"] for f in all_folders]
            console.print(f"[green]Found {len(folders_to_sync)} folders[/green]\n")

        # Sync each folder
        total_stats = {
            "created_local": 0,
            "created_remote": 0,
            "updated_local": 0,
            "updated_remote": 0,
            "deleted_local": 0,
            "deleted_remote": 0,
            "unchanged": 0,
        }

        for folder_name in folders_to_sync:
            try:
                console.print(f"[bold]Syncing folder:[/bold] {folder_name}")
                stats = await sync_engine.sync_folder(folder_name, folder_name)

                # Aggregate stats
                for key in total_stats:
                    total_stats[key] += stats[key]

                # Show folder stats
                if any(stats[k] > 0 for k in stats if k != "unchanged"):
                    console.print(
                        f"  [green]✓[/green] "
                        f"{stats['created_remote']} created, "
                        f"{stats['updated_remote']} updated, "
                        f"{stats['deleted_remote']} deleted "
                        f"(remote)"
                    )
                    console.print(
                        f"  [green]✓[/green] "
                        f"{stats['created_local']} created, "
                        f"{stats['updated_local']} updated, "
                        f"{stats['deleted_local']} deleted "
                        f"(local)"
                    )
                else:
                    console.print(f"  [dim]No changes needed ({stats['unchanged']} unchanged)[/dim]")

            except Exception as e:
                console.print(f"  [red]✗ Failed: {e}[/red]")
                logging.exception(f"Failed to sync folder {folder_name}")

        # Show summary
        console.print("\n[bold]Sync Summary[/bold]")
        table = Table()
        table.add_column("Operation", style="cyan")
        table.add_column("Local (Apple Notes)", style="green", justify="right")
        table.add_column("Remote (Markdown)", style="blue", justify="right")

        table.add_row("Created", str(total_stats["created_local"]), str(total_stats["created_remote"]))
        table.add_row("Updated", str(total_stats["updated_local"]), str(total_stats["updated_remote"]))
        table.add_row("Deleted", str(total_stats["deleted_local"]), str(total_stats["deleted_remote"]))
        table.add_row("Unchanged", str(total_stats["unchanged"]), str(total_stats["unchanged"]))

        console.print(table)

    # Run async sync
    try:
        asyncio.run(run_sync())
    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        logging.exception("Sync operation failed")
        raise typer.Exit(1) from e


@notes_app.command("list")
def notes_list(ctx: typer.Context) -> None:
    """List all Apple Notes folders."""
    cfg = ctx.obj["config"]

    async def run_list():
        # Initialize sync engine
        sync_engine = NotesSyncEngine(
            markdown_base_path=cfg.notes.remote_folder or Path("/tmp"),
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        # Get folders
        console.print("[cyan]Fetching folders from Apple Notes...[/cyan]\n")
        folders = await sync_engine.list_folders()

        if not folders:
            console.print("[yellow]No folders found in Apple Notes[/yellow]")
            return

        # Display folders in a table
        table = Table(title="Apple Notes Folders")
        table.add_column("Folder Name", style="cyan", no_wrap=True)
        table.add_column("UUID", style="dim")

        for folder in folders:
            table.add_row(folder["name"], folder["uuid"])

        console.print(table)
        console.print(f"\n[dim]Total: {len(folders)} folders[/dim]")

    # Run async list
    try:
        asyncio.run(run_list())
    except Exception as e:
        console.print(f"[red]Failed to list folders: {e}[/red]")
        logging.exception("List operation failed")
        raise typer.Exit(1) from e


@notes_app.command("status")
def notes_status(ctx: typer.Context) -> None:
    """Show notes synchronization status."""
    cfg = ctx.obj["config"]

    # Check if notes sync is enabled
    if not cfg.notes.enabled:
        console.print("[red]Notes sync is not enabled in configuration[/red]")
        return

    async def run_status():
        # Initialize sync engine
        sync_engine = NotesSyncEngine(
            markdown_base_path=cfg.notes.remote_folder or Path("/tmp"),
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        # Get sync status
        status = await sync_engine.get_sync_status()

        # Display status
        table = Table(title="Notes Sync Status")
        table.add_column("Property", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        table.add_row("Total Synced Notes", str(status["total_mappings"]))
        table.add_row("Remote Folder", str(cfg.notes.remote_folder))
        table.add_row("Database", str(cfg.db_path))

        console.print(table)

        if status["total_mappings"] == 0:
            console.print("\n[yellow]No notes have been synced yet[/yellow]")
            console.print("[dim]Run 'icloudbridge notes sync' to start syncing[/dim]")

    # Run async status
    try:
        asyncio.run(run_status())
    except Exception as e:
        console.print(f"[red]Failed to get status: {e}[/red]")
        logging.exception("Status operation failed")
        raise typer.Exit(1) from e


# Reminders subcommand group
reminders_app = typer.Typer(help="Manage reminders synchronization")
app.add_typer(reminders_app, name="reminders")


@reminders_app.command("sync")
def reminders_sync(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview changes without applying them",
    ),
) -> None:
    """Synchronize reminders."""
    console.print("[yellow]Reminders sync not yet implemented[/yellow]")
    if dry_run:
        console.print("[dim]Dry run mode enabled[/dim]")


@reminders_app.command("list")
def reminders_list(ctx: typer.Context) -> None:
    """List local reminder lists."""
    console.print("[yellow]Reminders list not yet implemented[/yellow]")


@reminders_app.command("status")
def reminders_status(ctx: typer.Context) -> None:
    """Show reminders sync status."""
    console.print("[yellow]Reminders status not yet implemented[/yellow]")


def main_entry() -> None:
    """Entry point for the CLI."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logging.exception("Unhandled exception")
        sys.exit(1)


if __name__ == "__main__":
    main_entry()
