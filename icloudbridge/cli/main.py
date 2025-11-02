"""Command-line interface for iCloudBridge."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from icloudbridge import __version__
from icloudbridge.core.config import load_config
from icloudbridge.core.reminders_sync import RemindersSyncEngine
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
    init: bool = typer.Option(
        False,
        "--init",
        "-i",
        help="Create a default configuration file",
    ),
) -> None:
    """Manage configuration."""
    cfg = ctx.obj["config"]

    if init:
        # Create default config file
        config_path = cfg.default_config_path

        if config_path.exists():
            console.print(f"[yellow]Config file already exists:[/yellow] {config_path}")
            overwrite = typer.confirm("Overwrite existing config?")
            if not overwrite:
                console.print("[dim]Config creation cancelled[/dim]")
                raise typer.Exit(0)

        # Create config with example values
        try:
            cfg.save_to_file(config_path)
            console.print(f"[green]✓ Config file created:[/green] {config_path}")
            console.print("\n[cyan]Example configuration:[/cyan]")
            console.print(f"[dim]{config_path}[/dim]\n")
            console.print("[yellow]Edit this file to configure iCloudBridge.[/yellow]")
            console.print("[dim]See the documentation for all available options.[/dim]")
        except ImportError:
            console.print("[red]Error: tomli_w not installed[/red]")
            console.print("[dim]Install with: pip install tomli-w[/dim]")
            raise typer.Exit(1)

        return

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
        console.print(
            "[dim]Use --init to create a default config file[/dim]",
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
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Sync specific folder only"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview changes without applying them"),
    skip_deletions: bool = typer.Option(False, "--skip-deletions", help="Skip all deletion operations"),
    deletion_threshold: int = typer.Option(5, "--deletion-threshold", help="Max deletions before confirmation (use -1 to disable)"),
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
        console.print("[cyan]DRY RUN MODE: Previewing changes only[/cyan]\n")

    async def run_sync():
        # Initialize sync engine
        sync_engine = NotesSyncEngine(
            markdown_base_path=cfg.notes.remote_folder,
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        # Automatically migrate any root-level notes to "Notes" folder
        if not dry_run:
            migrated = await sync_engine.migrate_root_notes_to_folder()
            if migrated > 0:
                console.print(f"[yellow]Migrated {migrated} root-level note(s) to 'Notes' folder[/yellow]\n")

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
            "would_delete_local": 0,
            "would_delete_remote": 0,
        }

        for folder_name in folders_to_sync:
            try:
                console.print(f"[bold]Syncing folder:[/bold] {folder_name}")
                stats = await sync_engine.sync_folder(
                    folder_name,
                    folder_name,
                    dry_run=dry_run,
                    skip_deletions=skip_deletions,
                    deletion_threshold=deletion_threshold,
                )

                # Aggregate stats
                for key in total_stats:
                    total_stats[key] += stats[key]

                # Show folder stats
                if dry_run:
                    # Show dry-run preview
                    if any(stats[k] > 0 for k in stats if k not in ["unchanged", "would_delete_local", "would_delete_remote"]) or stats["would_delete_local"] > 0 or stats["would_delete_remote"] > 0:
                        console.print(
                            f"  [yellow]Preview:[/yellow] "
                            f"{stats['created_remote']} would create, "
                            f"{stats['updated_remote']} would update, "
                            f"{stats['would_delete_remote']} would delete "
                            f"(remote)"
                        )
                        console.print(
                            f"  [yellow]Preview:[/yellow] "
                            f"{stats['created_local']} would create, "
                            f"{stats['updated_local']} would update, "
                            f"{stats['would_delete_local']} would delete "
                            f"(local)"
                        )
                    else:
                        console.print(f"  [dim]No changes needed ({stats['unchanged']} unchanged)[/dim]")
                elif any(stats[k] > 0 for k in stats if k != "unchanged"):
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

            except RuntimeError as e:
                # Check if it's a deletion threshold error
                if "Deletion threshold exceeded" in str(e):
                    console.print(f"  [red]✗ {e}[/red]")
                    console.print("  [yellow]Use --deletion-threshold -1 to bypass this check[/yellow]")
                    raise typer.Exit(1) from e
                else:
                    console.print(f"  [red]✗ Failed: {e}[/red]")
                    logging.exception(f"Failed to sync folder {folder_name}")
            except Exception as e:
                console.print(f"  [red]✗ Failed: {e}[/red]")
                logging.exception(f"Failed to sync folder {folder_name}")

        # Show summary
        if dry_run:
            console.print("\n[bold]Dry Run Summary (Preview Only)[/bold]")
        else:
            console.print("\n[bold]Sync Summary[/bold]")

        table = Table()
        table.add_column("Operation", style="cyan")
        table.add_column("Local (Apple Notes)", style="green", justify="right")
        table.add_column("Remote (Markdown)", style="blue", justify="right")

        if dry_run:
            table.add_row("Would Create", str(total_stats["created_local"]), str(total_stats["created_remote"]))
            table.add_row("Would Update", str(total_stats["updated_local"]), str(total_stats["updated_remote"]))
            table.add_row("Would Delete", str(total_stats["would_delete_local"]), str(total_stats["would_delete_remote"]))
            table.add_row("Unchanged", str(total_stats["unchanged"]), str(total_stats["unchanged"]))
        else:
            table.add_row("Created", str(total_stats["created_local"]), str(total_stats["created_remote"]))
            table.add_row("Updated", str(total_stats["updated_local"]), str(total_stats["updated_remote"]))
            table.add_row("Deleted", str(total_stats["deleted_local"]), str(total_stats["deleted_remote"]))
            table.add_row("Unchanged", str(total_stats["unchanged"]), str(total_stats["unchanged"]))

        console.print(table)

        if dry_run:
            console.print("\n[yellow]This was a dry run. No changes were made.[/yellow]")
            console.print("[dim]Run without --dry-run to apply these changes.[/dim]")

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


@notes_app.command("reset")
def notes_reset(
    ctx: typer.Context,
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Reset the sync database (clears all note mappings)."""
    cfg = ctx.obj["config"]

    # Confirm with user
    if not confirm:
        console.print("[yellow]⚠ Warning: This will clear all note sync mappings![/yellow]")
        console.print("[dim]Your notes will NOT be deleted, but the sync engine will treat")
        console.print("everything as 'new' on the next sync.[/dim]\n")

        response = typer.confirm("Are you sure you want to reset the database?")
        if not response:
            console.print("[dim]Reset cancelled[/dim]")
            raise typer.Exit(0)

    async def run_reset():
        # Initialize sync engine
        sync_engine = NotesSyncEngine(
            markdown_base_path=cfg.notes.remote_folder or Path("/tmp"),
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        # Clear all mappings
        await sync_engine.reset_database()

        console.print("[green]✓ Database reset successfully[/green]")
        console.print("[dim]Run 'icloudbridge notes sync' to start fresh[/dim]")

    # Run async reset
    try:
        asyncio.run(run_reset())
    except Exception as e:
        console.print(f"[red]Failed to reset database: {e}[/red]")
        logging.exception("Reset operation failed")
        raise typer.Exit(1) from e


# Reminders subcommand group
reminders_app = typer.Typer(help="Manage reminders synchronization")
app.add_typer(reminders_app, name="reminders")


@reminders_app.command("sync")
def reminders_sync(
    ctx: typer.Context,
    apple_calendar: Optional[str] = typer.Option(
        None,
        "--apple-calendar",
        "-a",
        help="Apple Reminders calendar/list to sync (manual mode)",
    ),
    caldav_calendar: Optional[str] = typer.Option(
        None,
        "--caldav-calendar",
        "-c",
        help="CalDAV calendar to sync with (manual mode)",
    ),
    auto: bool = typer.Option(
        None,
        "--auto/--no-auto",
        help="Auto-discover and sync all calendars (default: from config)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview changes without applying them",
    ),
    skip_deletions: bool = typer.Option(
        False,
        "--skip-deletions",
        help="Skip all deletion operations",
    ),
    deletion_threshold: int = typer.Option(
        5,
        "--deletion-threshold",
        help="Max deletions before confirmation (use -1 to disable)",
    ),
) -> None:
    """
    Synchronize reminders between Apple Reminders and CalDAV.

    By default (auto mode), syncs all calendars:
    - "Reminders" → "tasks" (NextCloud default)
    - Other Apple calendars → CalDAV calendars with matching names

    Use --apple-calendar and --caldav-calendar for manual single-calendar sync.
    """
    cfg = ctx.obj["config"]

    # Check if reminders sync is enabled
    if not cfg.reminders.enabled:
        console.print("[red]Reminders sync is not enabled in configuration[/red]")
        console.print("[dim]Enable it in your config file or use environment variables[/dim]")
        raise typer.Exit(1)

    # Check if CalDAV is configured
    if not cfg.reminders.caldav_url:
        console.print("[red]CalDAV URL is not configured[/red]")
        console.print("[dim]Set ICLOUDBRIDGE_REMINDERS__CALDAV_URL in your config[/dim]")
        raise typer.Exit(1)

    # Get password from keyring or config
    caldav_password = cfg.reminders.get_caldav_password()

    if not cfg.reminders.caldav_username or not caldav_password:
        console.print("[red]CalDAV credentials not configured[/red]")
        console.print("[dim]Set username with ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME[/dim]")
        console.print("[dim]Set password with: icloudbridge reminders set-password[/dim]")
        raise typer.Exit(1)

    # Determine sync mode
    use_auto = auto if auto is not None else (cfg.reminders.sync_mode == "auto")

    # Manual mode: specific calendar pair
    if apple_calendar and caldav_calendar:
        use_auto = False

    if dry_run:
        console.print("[cyan]DRY RUN MODE: Previewing changes only[/cyan]\n")

    async def run_sync():
        # Initialize sync engine
        sync_engine = RemindersSyncEngine(
            caldav_url=cfg.reminders.caldav_url,
            caldav_username=cfg.reminders.caldav_username,
            caldav_password=caldav_password,
            db_path=cfg.db_path,
        )
        await sync_engine.initialize()

        try:
            if use_auto:
                # Auto mode: discover and sync all calendars
                console.print("[bold cyan]Auto Mode:[/bold cyan] Discovering and syncing all calendars\n")

                all_stats = await sync_engine.discover_and_sync_all(
                    base_mappings=cfg.reminders.calendar_mappings,
                    dry_run=dry_run,
                    skip_deletions=skip_deletions,
                    deletion_threshold=deletion_threshold,
                )

                # Show summary table
                table = Table(title="Sync Results")
                table.add_column("Calendar Pair", style="cyan")
                table.add_column("Created", style="green", justify="right")
                table.add_column("Updated", style="yellow", justify="right")
                table.add_column("Deleted", style="red", justify="right")
                table.add_column("Unchanged", style="dim", justify="right")
                table.add_column("Errors", style="red bold", justify="right")

                total_stats = {
                    "created": 0,
                    "updated": 0,
                    "deleted": 0,
                    "unchanged": 0,
                    "errors": 0,
                }

                for pair_name, stats in all_stats.items():
                    created = stats['created_local'] + stats['created_remote']
                    updated = stats['updated_local'] + stats['updated_remote']
                    deleted = stats['deleted_local'] + stats['deleted_remote']

                    total_stats["created"] += created
                    total_stats["updated"] += updated
                    total_stats["deleted"] += deleted
                    total_stats["unchanged"] += stats['unchanged']
                    total_stats["errors"] += stats['errors']

                    table.add_row(
                        pair_name,
                        str(created),
                        str(updated),
                        str(deleted),
                        str(stats['unchanged']),
                        str(stats['errors']) if stats['errors'] > 0 else "-",
                    )

                console.print(table)
                console.print(f"\n[bold green]Total:[/bold green] {total_stats['created']} created, "
                             f"{total_stats['updated']} updated, {total_stats['deleted']} deleted, "
                             f"{total_stats['unchanged']} unchanged")
                if total_stats['errors'] > 0:
                    console.print(f"[red]Errors: {total_stats['errors']}[/red]")

            else:
                # Manual mode: single calendar pair
                # Use legacy config or CLI args
                if not apple_calendar:
                    apple_calendar = cfg.reminders.apple_calendar
                if not caldav_calendar:
                    caldav_calendar = cfg.reminders.caldav_calendar

                if not apple_calendar or not caldav_calendar:
                    console.print("[red]Manual mode requires --apple-calendar and --caldav-calendar[/red]")
                    console.print("[dim]Or use auto mode with --auto (or set sync_mode=auto in config)[/dim]")
                    raise typer.Exit(1)

                console.print(f"[cyan]Manual Mode:[/cyan] Syncing {apple_calendar} → {caldav_calendar}\n")

                stats = await sync_engine.sync_calendar(
                    apple_calendar_name=apple_calendar,
                    caldav_calendar_name=caldav_calendar,
                    dry_run=dry_run,
                    skip_deletions=skip_deletions,
                    deletion_threshold=deletion_threshold,
                )

                # Show stats
                console.print(f"\n[bold green]Sync completed![/bold green]")
                console.print(f"  Created in Apple Reminders: {stats['created_local']}")
                console.print(f"  Created in CalDAV: {stats['created_remote']}")
                console.print(f"  Updated in Apple Reminders: {stats['updated_local']}")
                console.print(f"  Updated in CalDAV: {stats['updated_remote']}")
                console.print(f"  Deleted from Apple Reminders: {stats['deleted_local']}")
                console.print(f"  Deleted from CalDAV: {stats['deleted_remote']}")
                console.print(f"  Unchanged: {stats['unchanged']}")
                if stats['errors'] > 0:
                    console.print(f"  [red]Errors: {stats['errors']}[/red]")

        except Exception as e:
            console.print(f"[red]Sync failed: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(run_sync())


@reminders_app.command("list")
def reminders_list(ctx: typer.Context) -> None:
    """List Apple Reminders calendars/lists."""
    cfg = ctx.obj["config"]

    if not cfg.reminders.enabled:
        console.print("[red]Reminders sync is not enabled in configuration[/red]")
        raise typer.Exit(1)

    async def list_calendars():
        from icloudbridge.sources.reminders.eventkit import RemindersAdapter

        adapter = RemindersAdapter()
        await adapter.request_access()

        calendars = await adapter.list_calendars()

        if not calendars:
            console.print("[yellow]No reminder calendars found[/yellow]")
            return

        table = Table(title="Apple Reminders Calendars")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("UUID", style="dim")

        for cal in calendars:
            table.add_row(cal.title, cal.uuid)

        console.print(table)

    asyncio.run(list_calendars())


@reminders_app.command("status")
def reminders_status(ctx: typer.Context) -> None:
    """Show reminders sync status."""
    cfg = ctx.obj["config"]

    # Check configuration
    console.print("[bold]Reminders Sync Status[/bold]\n")

    if cfg.reminders.enabled:
        console.print("✓ Reminders sync enabled", style="green")
    else:
        console.print("✗ Reminders sync disabled", style="red")
        return

    if cfg.reminders.caldav_url:
        console.print(f"✓ CalDAV URL: {cfg.reminders.caldav_url}", style="green")
    else:
        console.print("✗ CalDAV URL not configured", style="red")

    if cfg.reminders.caldav_username:
        console.print(f"✓ CalDAV username: {cfg.reminders.caldav_username}", style="green")

        # Check password source
        password = cfg.reminders.get_caldav_password()
        if password:
            from icloudbridge.utils.credentials import CredentialStore

            cred_store = CredentialStore()
            if cred_store.has_caldav_password(cfg.reminders.caldav_username):
                console.print("✓ CalDAV password: stored in system keyring (secure)", style="green")
            else:
                console.print(
                    "✓ CalDAV password: configured in config/env (consider using keyring)",
                    style="yellow",
                )
        else:
            console.print("✗ CalDAV password not configured", style="red")
    else:
        console.print("✗ CalDAV username not configured", style="red")

    if cfg.reminders.apple_calendar:
        console.print(f"✓ Apple calendar: {cfg.reminders.apple_calendar}", style="green")
    else:
        console.print("ℹ Apple calendar not configured (can specify with --apple-calendar)", style="yellow")

    if cfg.reminders.caldav_calendar:
        console.print(f"✓ CalDAV calendar: {cfg.reminders.caldav_calendar}", style="green")
    else:
        console.print("ℹ CalDAV calendar not configured (can specify with --caldav-calendar)", style="yellow")

    console.print("\n[dim]Status: Ready[/dim]")


@reminders_app.command("reset")
def reminders_reset(
    ctx: typer.Context,
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Reset reminders sync database (clear all mappings)."""
    cfg = ctx.obj["config"]

    if not yes:
        console.print("[yellow]This will clear all reminder sync mappings from the database.[/yellow]")
        console.print("[dim]Your reminders will NOT be deleted, only the sync tracking.[/dim]\n")
        confirmed = typer.confirm("Are you sure you want to continue?")
        if not confirmed:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    async def reset_db():
        sync_engine = RemindersSyncEngine(
            caldav_url=cfg.reminders.caldav_url or "http://dummy.url",
            caldav_username=cfg.reminders.caldav_username or "dummy",
            caldav_password=cfg.reminders.caldav_password or "dummy",
            db_path=cfg.db_path,
        )
        await sync_engine.db.initialize()
        await sync_engine.reset_database()
        console.print("[green]✓ Database reset complete[/green]")

    asyncio.run(reset_db())


@reminders_app.command("set-password")
def reminders_set_password(
    ctx: typer.Context,
    username: Optional[str] = typer.Option(
        None,
        "--username",
        "-u",
        help="CalDAV username (default: from config)",
    ),
) -> None:
    """Store CalDAV password securely in system keyring."""
    from icloudbridge.utils.credentials import CredentialStore

    cfg = ctx.obj["config"]

    # Use config username if not specified
    if not username:
        username = cfg.reminders.caldav_username
        if not username:
            console.print("[red]Username not specified and not found in config[/red]")
            console.print("[dim]Use --username or set ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME[/dim]")
            raise typer.Exit(1)

    # Prompt for password (hidden input)
    password = typer.prompt(f"Enter CalDAV password for {username}", hide_input=True)
    password_confirm = typer.prompt("Confirm password", hide_input=True)

    if password != password_confirm:
        console.print("[red]Passwords do not match[/red]")
        raise typer.Exit(1)

    # Store in keyring
    try:
        cred_store = CredentialStore()
        cred_store.set_caldav_password(username, password)
        console.print(f"[green]✓ Password stored securely for user: {username}[/green]")
        console.print("[dim]You can now remove CALDAV_PASSWORD from your config/environment[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to store password: {e}[/red]")
        raise typer.Exit(1)


@reminders_app.command("delete-password")
def reminders_delete_password(
    ctx: typer.Context,
    username: Optional[str] = typer.Option(
        None,
        "--username",
        "-u",
        help="CalDAV username (default: from config)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Delete CalDAV password from system keyring."""
    from icloudbridge.utils.credentials import CredentialStore

    cfg = ctx.obj["config"]

    # Use config username if not specified
    if not username:
        username = cfg.reminders.caldav_username
        if not username:
            console.print("[red]Username not specified and not found in config[/red]")
            console.print("[dim]Use --username or set ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME[/dim]")
            raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(f"Delete stored password for {username}?")
        if not confirmed:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Delete from keyring
    try:
        cred_store = CredentialStore()
        if cred_store.delete_caldav_password(username):
            console.print(f"[green]✓ Password deleted for user: {username}[/green]")
        else:
            console.print(f"[yellow]No password found for user: {username}[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to delete password: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# PASSWORD COMMANDS
# ============================================================================


@app.command()
def passwords_import_apple(
    ctx: typer.Context,
    csv_file: Path = typer.Argument(..., help="Apple Passwords CSV export file"),
) -> None:
    """Import passwords from Apple Passwords CSV export."""
    import asyncio
    from datetime import datetime

    from ..core.passwords_sync import PasswordsSyncEngine
    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Apple Passwords Import", style="bold blue"))

    # Validate file exists
    if not csv_file.exists():
        console.print(f"[red]Error: File not found: {csv_file}[/red]")
        raise typer.Exit(1)

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def run_import():
        await db.initialize()
        engine = PasswordsSyncEngine(db)
        return await engine.import_apple_csv(csv_file)

    try:
        stats = asyncio.run(run_import())

        # Display results
        table = Table(title="Import Results")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right", style="green")

        table.add_row("Total processed", str(stats["total_processed"]))
        table.add_row("New entries", str(stats["new"]))
        table.add_row("Updated entries", str(stats["updated"]))
        table.add_row("Duplicates skipped", str(stats["duplicates"]))
        table.add_row("Unchanged", str(stats["unchanged"]))
        if stats["errors"] > 0:
            table.add_row("Errors", str(stats["errors"]), style="red")

        console.print(table)

        console.print(f"\n[green]✅ Import complete[/green]")
        console.print(f"   Database: {db_path}")
        console.print(f"   Last import: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Security warning
        console.print(
            "\n[yellow]⚠️  SECURITY WARNING[/yellow]\n"
            "   CSV file contains plaintext passwords!\n"
            f"   Delete immediately: {csv_file}\n"
        )

        # Next step suggestion
        console.print(
            "[dim]💡 Next step: Generate Bitwarden import file[/dim]\n"
            f"   → icloudbridge passwords-export-bitwarden -o bitwarden.csv --apple-csv {csv_file}"
        )

    except Exception as e:
        console.print(f"[red]Error importing passwords: {e}[/red]")
        logging.exception("Import failed")
        raise typer.Exit(1)


@app.command()
def passwords_export_bitwarden(
    ctx: typer.Context,
    output: Path = typer.Option(..., "-o", "--output", help="Output CSV file"),
    apple_csv: Path = typer.Option(..., help="Original Apple Passwords CSV"),
) -> None:
    """Generate Bitwarden-formatted CSV for import."""
    import asyncio
    from datetime import datetime

    from ..core.passwords_sync import PasswordsSyncEngine
    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Bitwarden CSV Export", style="bold blue"))

    # Validate input file exists
    if not apple_csv.exists():
        console.print(f"[red]Error: Apple CSV not found: {apple_csv}[/red]")
        raise typer.Exit(1)

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def run_export():
        await db.initialize()
        engine = PasswordsSyncEngine(db)
        return await engine.export_bitwarden_csv(output, apple_csv)

    try:
        count = asyncio.run(run_export())

        console.print(f"[green]✅ Bitwarden CSV generated[/green]")
        console.print(f"   File: {output}")
        console.print(f"   Entries: {count}")
        console.print(f"   Permissions: 0600 (owner read/write only)")

        # Security warning
        console.print(
            "\n[yellow]⚠️  SECURITY WARNING[/yellow]\n"
            "   Generated CSV contains plaintext passwords!\n"
            f"   1. Import to Bitwarden immediately\n"
            f"   2. Delete file: rm {output}\n"
        )

        # Next steps
        console.print(
            "[dim]💡 Import to Bitwarden:[/dim]\n"
            "   Settings → Import Data → Bitwarden (csv)\n"
            f"   Then delete both CSV files!"
        )

    except Exception as e:
        console.print(f"[red]Error exporting to Bitwarden: {e}[/red]")
        logging.exception("Export failed")
        raise typer.Exit(1)


@app.command()
def passwords_import_bitwarden(
    ctx: typer.Context,
    csv_file: Path = typer.Argument(..., help="Bitwarden CSV export file"),
) -> None:
    """Import passwords from Bitwarden CSV export."""
    import asyncio
    from datetime import datetime

    from ..core.passwords_sync import PasswordsSyncEngine
    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Bitwarden Import", style="bold blue"))

    # Validate file exists
    if not csv_file.exists():
        console.print(f"[red]Error: File not found: {csv_file}[/red]")
        raise typer.Exit(1)

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def run_import():
        await db.initialize()
        engine = PasswordsSyncEngine(db)
        return await engine.import_bitwarden_csv(csv_file)

    try:
        stats = asyncio.run(run_import())

        # Display results
        table = Table(title="Import Results")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right", style="green")

        table.add_row("Total processed", str(stats["total_processed"]))
        table.add_row("New entries", str(stats["new"]))
        table.add_row("Updated entries", str(stats["updated"]))
        table.add_row("Duplicates skipped", str(stats["duplicates"]))
        table.add_row("Unchanged", str(stats["unchanged"]))
        if stats["errors"] > 0:
            table.add_row("Errors", str(stats["errors"]), style="red")

        console.print(table)

        console.print(f"\n[green]✅ Import complete[/green]")
        console.print(f"   Database: {db_path}")
        console.print(f"   Last import: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Security warning
        console.print(
            "\n[yellow]⚠️  SECURITY WARNING[/yellow]\n"
            "   CSV file contains plaintext passwords!\n"
            f"   Delete immediately: {csv_file}\n"
        )

        # Next step suggestion
        console.print(
            "[dim]💡 Next step: Generate Apple Passwords import file[/dim]\n"
            f"   → icloudbridge passwords-export-apple -o apple-import.csv --bitwarden-csv {csv_file}"
        )

    except Exception as e:
        console.print(f"[red]Error importing passwords: {e}[/red]")
        logging.exception("Import failed")
        raise typer.Exit(1)


@app.command()
def passwords_export_apple(
    ctx: typer.Context,
    output: Path = typer.Option(..., "-o", "--output", help="Output CSV file"),
    bitwarden_csv: Path = typer.Option(..., help="Original Bitwarden CSV export"),
) -> None:
    """Generate Apple Passwords CSV for entries only in Bitwarden (not in Apple)."""
    import asyncio

    from ..core.passwords_sync import PasswordsSyncEngine
    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Apple Passwords Export", style="bold blue"))

    # Validate input file exists
    if not bitwarden_csv.exists():
        console.print(f"[red]Error: Bitwarden CSV not found: {bitwarden_csv}[/red]")
        raise typer.Exit(1)

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def run_export():
        await db.initialize()
        engine = PasswordsSyncEngine(db)
        return await engine.export_apple_csv(output, bitwarden_csv)

    try:
        count = asyncio.run(run_export())

        if count == 0:
            console.print("[yellow]No new passwords found in Bitwarden[/yellow]")
            console.print("   All Bitwarden passwords already exist in Apple Passwords")
        else:
            console.print(f"[green]✅ Apple Passwords CSV generated[/green]")
            console.print(f"   File: {output}")
            console.print(f"   New entries: {count}")
            console.print(f"   Permissions: 0600 (owner read/write only)")

            # Security warning
            console.print(
                "\n[yellow]⚠️  SECURITY WARNING[/yellow]\n"
                "   Generated CSV contains plaintext passwords!\n"
                f"   1. Import to Apple Passwords immediately\n"
                f"   2. Delete file: rm {output}\n"
            )

            # Instructions
            console.print(
                "[dim]💡 Import to Apple Passwords:[/dim]\n"
                "   1. Open Passwords app\n"
                "   2. File → Import Passwords\n"
                f"   3. Select {output}\n"
                "   4. Delete both CSV files!"
            )

    except Exception as e:
        console.print(f"[red]Error exporting to Apple format: {e}[/red]")
        logging.exception("Export failed")
        raise typer.Exit(1)


@app.command()
def passwords_status(ctx: typer.Context) -> None:
    """Show password sync status."""
    import asyncio
    from datetime import datetime

    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Password Sync Status", style="bold blue"))

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def get_status():
        await db.initialize()

        # Get statistics
        stats = await db.get_stats()

        # Get last syncs
        apple_import = await db.get_last_sync("apple_import")
        bitwarden_export = await db.get_last_sync("bitwarden_export")
        bitwarden_import = await db.get_last_sync("bitwarden_import")
        apple_export = await db.get_last_sync("apple_export")

        return {
            "stats": stats,
            "apple_import": apple_import,
            "bitwarden_export": bitwarden_export,
            "bitwarden_import": bitwarden_import,
            "apple_export": apple_export,
        }

    try:
        data = asyncio.run(get_status())
        stats = data["stats"]

        # Display statistics
        table = Table(title="Database Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="green")

        table.add_row("Total entries", str(stats["total"]))
        for source, count in stats["by_source"].items():
            table.add_row(f"  From {source}", str(count))

        console.print(table)

        # Display last syncs
        def format_timestamp(ts: float | None) -> str:
            if ts is None:
                return "Never"
            dt = datetime.fromtimestamp(ts)
            now = datetime.now()
            delta = now - dt
            if delta.days > 0:
                return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({delta.days} days ago)"
            elif delta.seconds > 3600:
                hours = delta.seconds // 3600
                return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({hours} hours ago)"
            else:
                minutes = delta.seconds // 60
                return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({minutes} minutes ago)"

        sync_table = Table(title="Last Syncs")
        sync_table.add_column("Operation", style="cyan")
        sync_table.add_column("Timestamp", style="yellow")

        sync_table.add_row(
            "Apple import",
            format_timestamp(data["apple_import"]["timestamp"] if data["apple_import"] else None),
        )
        sync_table.add_row(
            "Bitwarden export",
            format_timestamp(data["bitwarden_export"]["timestamp"] if data["bitwarden_export"] else None),
        )
        sync_table.add_row(
            "Bitwarden import",
            format_timestamp(data["bitwarden_import"]["timestamp"] if data["bitwarden_import"] else None),
        )
        sync_table.add_row(
            "Apple export",
            format_timestamp(data["apple_export"]["timestamp"] if data["apple_export"] else None),
        )

        console.print(sync_table)

        console.print(f"\n[dim]Database: {db_path}[/dim]")

    except Exception as e:
        console.print(f"[red]Error retrieving status: {e}[/red]")
        logging.exception("Status failed")
        raise typer.Exit(1)


@app.command()
def passwords_set_vaultwarden_credentials(ctx: typer.Context) -> None:
    """Set VaultWarden credentials in system keyring."""
    from getpass import getpass

    from ..utils.credentials import CredentialStore

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 VaultWarden Credentials Setup", style="bold blue"))

    # Get VaultWarden URL from config or prompt
    url = cfg.passwords.vaultwarden_url
    if not url:
        url = typer.prompt("VaultWarden URL (e.g., https://vault.example.com)")

        # Update config
        cfg.passwords.vaultwarden_url = url
        try:
            cfg.ensure_data_dir()
            config_path = cfg.general.data_dir / "config.toml"
            cfg.save_to_file(config_path)
            console.print(f"[dim]Saved URL to config: {config_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not save URL to config: {e}[/yellow]")

    # Get email
    email = cfg.passwords.vaultwarden_email
    if not email:
        email = typer.prompt("VaultWarden Email")

    # Get password (securely)
    console.print("\n[dim]Enter VaultWarden password (input hidden):[/dim]")
    password = getpass("Password: ")
    password_confirm = getpass("Confirm password: ")

    if password != password_confirm:
        console.print("[red]❌ Passwords do not match[/red]")
        raise typer.Exit(1)

    # Optional: client ID and secret
    console.print("\n[dim]OAuth client ID and secret (optional, press Enter to skip):[/dim]")
    client_id = typer.prompt("Client ID", default="", show_default=False) or None
    client_secret = None
    if client_id:
        client_secret = getpass("Client Secret: ") or None

    # Store in keyring
    try:
        cred_store = CredentialStore()
        cred_store.set_vaultwarden_credentials(email, password, client_id, client_secret)

        console.print(f"\n[green]✅ VaultWarden credentials stored securely[/green]")
        console.print(f"   Email: {email}")
        console.print(f"   URL: {url}")
        if client_id:
            console.print(f"   Client ID: {client_id}")

        console.print("\n[dim]💡 Test connection with:[/dim]")
        console.print(f"   icloudbridge passwords-sync --apple-csv <path/to/passwords.csv>")

    except Exception as e:
        console.print(f"[red]❌ Failed to store credentials: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def passwords_delete_vaultwarden_credentials(
    ctx: typer.Context,
    email: str | None = typer.Option(None, "--email", help="VaultWarden email"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
) -> None:
    """Delete VaultWarden credentials from system keyring."""
    from ..utils.credentials import CredentialStore

    cfg = ctx.obj["config"]

    # Get email from config if not provided
    if not email:
        email = cfg.passwords.vaultwarden_email
        if not email:
            console.print("[red]Email not specified and not found in config[/red]")
            console.print("[dim]Use --email or set ICLOUDBRIDGE_PASSWORDS__VAULTWARDEN_EMAIL[/dim]")
            raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(f"Delete stored credentials for {email}?")
        if not confirmed:
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    # Delete from keyring
    try:
        cred_store = CredentialStore()
        if cred_store.delete_vaultwarden_credentials(email):
            console.print(f"[green]✓ Credentials deleted for: {email}[/green]")
        else:
            console.print(f"[yellow]No credentials found for: {email}[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to delete credentials: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def passwords_sync(
    ctx: typer.Context,
    apple_csv: Path = typer.Option(..., help="Apple Passwords CSV export"),
    output: Path | None = typer.Option(None, "-o", "--output", help="Output path for Apple CSV (default: data_dir/apple-import.csv)"),
) -> None:
    """Full auto-sync: Apple → VaultWarden (push) and VaultWarden → Apple (pull)."""
    import asyncio

    from ..core.passwords_sync import PasswordsSyncEngine
    from ..sources.passwords.vaultwarden_api import VaultwardenAPIClient
    from ..utils.credentials import CredentialStore
    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    console.print(Panel.fit("🔐 Password Full Auto-Sync", style="bold blue"))

    # Validate Apple CSV exists
    if not apple_csv.exists():
        console.print(f"[red]Error: Apple CSV not found: {apple_csv}[/red]")
        raise typer.Exit(1)

    # Get VaultWarden configuration
    url = cfg.passwords.vaultwarden_url
    email = cfg.passwords.vaultwarden_email

    if not url:
        console.print("[red]VaultWarden URL not configured[/red]")
        console.print("[dim]Set with: icloudbridge passwords-set-vaultwarden-credentials[/dim]")
        raise typer.Exit(1)

    if not email:
        console.print("[red]VaultWarden email not configured[/red]")
        console.print("[dim]Set with: icloudbridge passwords-set-vaultwarden-credentials[/dim]")
        raise typer.Exit(1)

    # Get credentials
    credentials = cfg.passwords.get_vaultwarden_credentials()
    if not credentials:
        console.print("[red]VaultWarden credentials not found[/red]")
        console.print("[dim]Set with: icloudbridge passwords-set-vaultwarden-credentials[/dim]")
        raise typer.Exit(1)

    # Initialize database
    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def run_sync():
        await db.initialize()

        # Create and authenticate VaultWarden client
        vw_client = VaultwardenAPIClient(
            url=url,
            email=credentials["email"],
            password=credentials["password"],
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
        )

        console.print("[dim]Authenticating with VaultWarden...[/dim]")
        await vw_client.authenticate()

        # Run full sync
        engine = PasswordsSyncEngine(db)
        return await engine.sync(apple_csv, vw_client, output)

    try:
        stats = asyncio.run(run_sync())

        # Display results
        console.print("\n" + "=" * 60)
        console.print("📤 [bold]Apple → VaultWarden (Push)[/bold]")
        console.print("=" * 60)

        push_table = Table(show_header=False)
        push_table.add_column("Metric", style="cyan")
        push_table.add_column("Count", justify="right", style="green")

        push_stats = stats["push"]
        push_table.add_row("Created", str(push_stats.get("created", 0)))
        push_table.add_row("Updated", str(push_stats.get("updated", 0)))
        push_table.add_row("Skipped (unchanged)", str(push_stats.get("skipped", 0)))
        if push_stats.get("failed", 0) > 0:
            push_table.add_row("Failed", str(push_stats["failed"]), style="red")

        console.print(push_table)

        console.print("\n" + "=" * 60)
        console.print("📥 [bold]VaultWarden → Apple (Pull)[/bold]")
        console.print("=" * 60)

        pull_stats = stats["pull"]
        new_entries = pull_stats.get("new_entries", 0)

        if new_entries > 0:
            console.print(f"[green]✅ Generated Apple CSV with {new_entries} new entries[/green]")
            console.print(f"   File: {pull_stats['output_file']}")

            console.print("\n[yellow]⚠️  Manual step required:[/yellow]")
            console.print(f"   1. Open Passwords app")
            console.print(f"   2. File → Import Passwords")
            console.print(f"   3. Select: {pull_stats['output_file']}")
            console.print(f"   4. Delete CSV file after import")
        else:
            console.print("[dim]No new passwords from VaultWarden[/dim]")

        console.print("\n" + "=" * 60)
        console.print(f"[bold green]✅ Sync complete in {stats['total_time']:.1f}s[/bold green]")
        console.print("=" * 60)

        # Security reminder
        console.print(
            "\n[yellow]⚠️  SECURITY REMINDER[/yellow]\n"
            "   Delete CSV files after import:\n"
            f"   → rm {apple_csv}"
        )
        if new_entries > 0:
            console.print(f"   → rm {pull_stats['output_file']}")

    except Exception as e:
        console.print(f"[red]❌ Sync failed: {e}[/red]")
        logging.exception("Sync failed")
        raise typer.Exit(1)


@app.command()
def passwords_reset(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    """Clear all password entries from database."""
    import asyncio

    from ..utils.db import PasswordsDB

    cfg = ctx.obj["config"]

    if not yes:
        confirm = typer.confirm(
            "⚠️  This will delete all password entries from the database. Continue?"
        )
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            raise typer.Exit(0)

    db_path = cfg.general.data_dir / "passwords.db"
    db = PasswordsDB(db_path)

    async def reset():
        await db.initialize()
        await db.clear_all_entries()

    try:
        asyncio.run(reset())
        console.print("[green]✅ Password database reset complete[/green]")
    except Exception as e:
        console.print(f"[red]Error resetting database: {e}[/red]")
        logging.exception("Reset failed")
        raise typer.Exit(1)


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
