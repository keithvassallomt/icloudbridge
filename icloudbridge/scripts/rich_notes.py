"""Thin wrapper to run the Ruby notes ripper via Poetry."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from icloudbridge.utils.logging import log_subprocess_output

# How much ripper output to keep for the error message when it exits non-zero.
_ERROR_TAIL_LINES = 12


class RichRipperError(subprocess.CalledProcessError):
    """The Ruby ripper failed, with the tail of its output attached.

    A bare ``CalledProcessError`` reports only "returned non-zero exit status
    1", which sent one investigation chasing the Notes folder named in the
    surrounding sync error while the real message - a Ruby startup failure -
    sat unnoticed in the debug log. Still a ``CalledProcessError`` so existing
    handlers keep catching it.
    """

    def __init__(self, returncode: int, cmd: list[str], output_tail: str) -> None:
        super().__init__(returncode, cmd)
        self.output_tail = output_tail

    def __str__(self) -> str:
        detail = f"\n{self.output_tail}" if self.output_tail else ""
        return f"Notes ripper failed (exit {self.returncode}):{detail}"


def _bundler_version_args() -> list[str]:
    """``_x.y.z_`` selector for the Bundler the runtime installer pinned.

    Only used when the app tells us which Bundler it built (it guarantees that
    version exists). A dev checkout runs whatever ``bundle`` is on PATH, where
    demanding an exact version would just fail.
    """
    version = (os.environ.get("ICLOUDBRIDGE_BUNDLER_VERSION") or "").strip()
    return [f"_{version}_"] if version else []


def _ruby_interpreter(env: dict[str, str]) -> str:
    """The interpreter to run the ripper with, by absolute path where possible.

    ``bundle exec ruby`` resolves the bare word "ruby" through PATH. A
    GUI-launched app inherits only /usr/bin:/bin:/usr/sbin:/sbin, where `ruby`
    is macOS's own 2.6 - far too old for a modern Bundler, which dies with
    ``uninitialized constant Gem::Resolver::APISet::GemParser`` before the
    ripper starts. The managed `bundle` binstub has an absolute shebang, so the
    install and Bundler itself look healthy; only the child interpreter is wrong.
    """
    explicit = (env.get("ICLOUDBRIDGE_RUBY_PATH") or "").strip()
    if explicit and Path(explicit).exists():
        return explicit

    # An app-managed run whose installer predates ICLOUDBRIDGE_RUBY_PATH.
    if env.get("ICLOUDBRIDGE_BUNDLE_PATH"):
        for candidate in (
            Path("/opt/homebrew/opt/ruby/bin/ruby"),
            Path("/usr/local/opt/ruby/bin/ruby"),
        ):
            if candidate.exists():
                return str(candidate)

    # Dev checkout: whichever ruby the developer's PATH selects is correct.
    return "ruby"


def _build_ripper_command(extra_args: list[str]) -> tuple[list[str], dict[str, str], Path]:
    repo_root = Path(__file__).resolve().parents[2]
    ripper_dir = repo_root / "tools" / "notes_cloud_ripper"
    gemfile = ripper_dir / "Gemfile"
    script = ripper_dir / "notes_cloud_ripper.rb"

    if not gemfile.exists() or not script.exists():
        raise FileNotFoundError(
            "Expected notes_cloud_ripper assets under tools/notes_cloud_ripper (Gemfile + script)."
        )

    env = os.environ.copy()
    env["BUNDLE_GEMFILE"] = str(gemfile)

    # Never forward an inherited RUBYOPT into the managed ripper. Ruby splits
    # the variable on whitespace, so any value we did not construct ourselves -
    # a user's shell, a launchd entry, a wrapper script - can abort Ruby before
    # the script runs. Bundler sets its own RUBYOPT for the child regardless.
    env.pop("RUBYOPT", None)

    # The RubyGems compat shim goes in argv, not RUBYOPT: argv entries are
    # passed as distinct arguments and stay intact when the path contains a
    # space (an app installed at "/Applications/iCloudBridge Beta.app", say).
    # Ruby also processes argv requires before RUBYOPT ones, so the shim now
    # lands before bundler/setup rather than after it - which is the order a
    # RubyGems compatibility patch wants anyway.
    interpreter = _ruby_interpreter(env)
    ruby_args = [interpreter]
    patch_file = ripper_dir / "compat" / "rubygems_patch.rb"
    if patch_file.exists():
        ruby_args.append(f"-r{patch_file}")

    bundle_exe = env.get("ICLOUDBRIDGE_BUNDLE_PATH")
    if bundle_exe:
        bundle_cmd = [bundle_exe]
        bundle_dir = Path(bundle_exe).parent
        env["PATH"] = f"{bundle_dir}:{env.get('PATH', '')}"
    else:
        candidate_paths = [
            Path("/opt/homebrew/opt/ruby/bin/bundle"),
            Path.home() / ".rbenv" / "shims" / "bundle",
            Path("/opt/homebrew/bin/bundle"),
            Path("/usr/local/bin/bundle"),
            Path.home() / ".rubies" / "ruby-3.3.1" / "bin" / "bundle",
        ]
        bundle_path = next((p for p in candidate_paths if p.exists()), None)
        if bundle_path:
            bundle_cmd = [str(bundle_path)]
            env["PATH"] = f"{bundle_path.parent}:{env.get('PATH', '')}"
        else:
            bundle_cmd = ["bundle"]

    # Last, so it wins: nested `ruby` lookups (the ripper's own subprocesses,
    # gem tooling) must land on the interpreter we chose, not on whatever the
    # bundle directory or the inherited PATH happens to offer first.
    if interpreter != "ruby":
        ruby_dir = str(Path(interpreter).parent)
        remaining = [p for p in env.get("PATH", "").split(":") if p and p != ruby_dir]
        env["PATH"] = ":".join([ruby_dir, *remaining])

    # Always force single output folder (-g) and UUID identifiers for stability.
    forced_flags = ["-g", "--uuid"]

    cmd = [
        *bundle_cmd,
        *_bundler_version_args(),
        "exec",
        *ruby_args,
        str(script),
        *forced_flags,
        *extra_args,
    ]

    return cmd, env, repo_root


def run_rich_ripper(
    extra_args: list[str],
    *,
    log_stream: logging.Logger | None = None,
    log_category: str = "notes_ripper",
    log_level: str = "DEBUG",
) -> None:
    cmd, env, repo_root = _build_ripper_command(extra_args)
    if log_stream is None:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout or "")
            raise RichRipperError(result.returncode, cmd, _tail(result.stdout or ""))
        return

    process = subprocess.Popen(
        cmd,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    tail: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)
    log_subprocess_output(
        process,
        log_stream,
        category=log_category,
        level=log_level,
        on_line=tail.append,
    )
    retcode = process.wait()
    if retcode != 0:
        raise RichRipperError(retcode, cmd, "\n".join(tail))


def _tail(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-_ERROR_TAIL_LINES:])


def main() -> None:
    run_rich_ripper(sys.argv[1:])


if __name__ == "__main__":
    main()
