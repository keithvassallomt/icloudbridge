"""Thin wrapper to run the Ruby notes ripper via Poetry."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from icloudbridge.utils.logging import log_subprocess_output


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
    patch_file = ripper_dir / "compat" / "rubygems_patch.rb"
    rubyopt = env.get("RUBYOPT", "").strip()
    if patch_file.exists():
        injection = f"-r{patch_file}"
        env["RUBYOPT"] = f"{rubyopt} {injection}".strip()

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

    # Always force single output folder (-g) and UUID identifiers for stability.
    forced_flags = ["-g", "--uuid"]

    cmd = [
        *bundle_cmd,
        "exec",
        "ruby",
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
        subprocess.run(cmd, cwd=repo_root, env=env, check=True)
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
    log_subprocess_output(process, log_stream, category=log_category, level=log_level)
    retcode = process.wait()
    if retcode != 0:
        raise subprocess.CalledProcessError(retcode, cmd)


def main() -> None:
    run_rich_ripper(sys.argv[1:])


if __name__ == "__main__":
    main()
