"""End-to-end checks against a real Ruby for the RUBYOPT whitespace failure.

Skipped when no Ruby is installed. These assert the *language* behaviour the
runtime layout depends on, so that a future Ruby release changing it is caught
here rather than in a user's Notes sync.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RUBY = shutil.which("ruby") or "/opt/homebrew/opt/ruby/bin/ruby"

pytestmark = pytest.mark.skipif(
    not Path(RUBY).exists(), reason="no Ruby interpreter available"
)


@pytest.fixture
def patch_in_a_space_path(tmp_path):
    """A require-able file whose absolute path contains a space."""
    directory = tmp_path / "with space"
    directory.mkdir()
    patch = directory / "patch.rb"
    patch.write_text('$stderr.puts "PATCH LOADED"\n')
    return patch


def test_rubyopt_breaks_on_a_space_containing_path(patch_in_a_space_path):
    """The bug itself: Ruby cannot parse a RUBYOPT path containing a space."""
    result = subprocess.run(
        [RUBY, "-e", "puts :ok"],
        env={"RUBYOPT": f"-r{patch_in_a_space_path}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    # Ruby reports whichever letter follows the space, so match the shape.
    assert "invalid" in result.stderr.lower()
    assert "RUBYOPT" in result.stderr or "option" in result.stderr


def test_argv_require_survives_a_space_containing_path(patch_in_a_space_path):
    """The fix: the same path passed as an argv entry loads fine.

    This is what protects an app installed at, say,
    "/Applications/iCloudBridge Beta.app".
    """
    result = subprocess.run(
        [RUBY, f"-r{patch_in_a_space_path}", "-e", "puts :ok"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "PATCH LOADED" in result.stderr
    assert "ok" in result.stdout


def test_argv_requires_load_before_rubyopt_requires(tmp_path):
    """Order matters: the compat shim must land before bundler/setup.

    Bundler unshifts its own ``-rbundler/setup`` onto RUBYOPT, so moving our
    shim from RUBYOPT into argv moves it *earlier*, not later.
    """
    first = tmp_path / "from_argv.rb"
    first.write_text('$stderr.puts "ARGV"\n')
    second = tmp_path / "from_rubyopt.rb"
    second.write_text('$stderr.puts "RUBYOPT"\n')

    result = subprocess.run(
        [RUBY, f"-r{first}", "-e", "puts :ok"],
        env={"RUBYOPT": f"-r{second}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.index("ARGV") < result.stderr.index("RUBYOPT")
