"""Regression tests for the Notes ripper command construction.

These cover the failure described in the RUBYOPT incident: Ruby splits the
``RUBYOPT`` environment variable on whitespace, so any absolute path placed in
it breaks as soon as the path contains a space.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from icloudbridge.scripts.rich_notes import (
    RichRipperError,
    _build_ripper_command,
)

RIPPER_DIR = Path(__file__).resolve().parents[1] / "tools" / "notes_cloud_ripper"
PATCH_FILE = RIPPER_DIR / "compat" / "rubygems_patch.rb"


@pytest.fixture
def clean_env(monkeypatch):
    """Drop the runtime env vars so each test sets only what it means to."""
    for var in (
        "RUBYOPT",
        "ICLOUDBRIDGE_BUNDLE_PATH",
        "ICLOUDBRIDGE_BUNDLER_VERSION",
    ):
        monkeypatch.delenv(var, raising=False)


def test_inherited_rubyopt_is_not_forwarded(monkeypatch, clean_env):
    """A hostile RUBYOPT from the shell or launchd must not reach the child."""
    monkeypatch.setenv("RUBYOPT", "-S")

    _, env, _ = _build_ripper_command([])

    assert "RUBYOPT" not in env


def test_rubyopt_is_never_constructed_by_us(monkeypatch, clean_env):
    """We must not put any of our own absolute paths into RUBYOPT."""
    _, env, _ = _build_ripper_command([])

    assert "RUBYOPT" not in env


@pytest.mark.skipif(not PATCH_FILE.exists(), reason="compat patch not present")
def test_compat_patch_is_passed_as_a_ruby_argv_entry(clean_env):
    """The shim rides in argv, where a space in the path is harmless."""
    cmd, _, _ = _build_ripper_command([])

    injection = f"-r{PATCH_FILE}"
    assert injection in cmd

    # It must be an option to ruby, i.e. after "ruby" and before the script.
    ruby_at = cmd.index("ruby")
    patch_at = cmd.index(injection)
    script_at = cmd.index(str(RIPPER_DIR / "notes_cloud_ripper.rb"))
    assert ruby_at < patch_at < script_at

    # And it must be one argv element, not something a shell would resplit.
    assert sum(1 for part in cmd if part == injection) == 1


def test_bundler_version_is_pinned_when_the_app_supplies_one(monkeypatch, clean_env):
    """The app names the Bundler its installer built, rather than PATH's."""
    monkeypatch.setenv("ICLOUDBRIDGE_BUNDLER_VERSION", "4.0.3")

    cmd, _, _ = _build_ripper_command([])

    assert "_4.0.3_" in cmd
    # Bundler requires the version selector before the subcommand.
    assert cmd.index("_4.0.3_") < cmd.index("exec")


def test_bundler_version_is_not_pinned_in_a_dev_checkout(clean_env):
    """Without the app's env var, demanding an exact version would just fail."""
    cmd, _, _ = _build_ripper_command([])

    assert not any(part.startswith("_") and part.endswith("_") for part in cmd)


def test_managed_bundle_executable_is_used_when_provided(monkeypatch, clean_env, tmp_path):
    bundle = tmp_path / "bin" / "bundle"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("#!/bin/sh\n")
    bundle.chmod(0o755)
    monkeypatch.setenv("ICLOUDBRIDGE_BUNDLE_PATH", str(bundle))

    cmd, env, _ = _build_ripper_command([])

    assert cmd[0] == str(bundle)
    # On PATH, but the chosen interpreter's directory takes precedence over it.
    assert str(bundle.parent) in env["PATH"].split(":")


def test_error_carries_the_output_tail():
    """The real Ruby message must reach the user, not just the exit status."""
    ruby_error = "ruby: invalid switch in RUBYOPT: -S (RuntimeError)"
    err = RichRipperError(1, ["bundle", "exec", "ruby"], ruby_error)

    assert ruby_error in str(err)
    assert "exit 1" in str(err)
    # Existing handlers catch CalledProcessError; keep working for them.
    assert isinstance(err, subprocess.CalledProcessError)
    assert err.returncode == 1


class TestInterpreterResolution:
    """The ripper must never be run by a PATH-resolved `ruby`.

    A GUI-launched app inherits only /usr/bin:/bin:/usr/sbin:/sbin, where
    `ruby` is macOS's own 2.6. Bundler 4.x dies under it with `uninitialized
    constant Gem::Resolver::APISet::GemParser` before the ripper starts.
    """

    def test_explicit_interpreter_is_used(self, monkeypatch, clean_env, tmp_path):
        ruby = tmp_path / "bin" / "ruby"
        ruby.parent.mkdir(parents=True)
        ruby.write_text("#!/bin/sh\n")
        ruby.chmod(0o755)
        monkeypatch.setenv("ICLOUDBRIDGE_RUBY_PATH", str(ruby))

        cmd, env, _ = _build_ripper_command([])

        assert "ruby" not in cmd, "the bare word would resolve through PATH"
        assert str(ruby) in cmd
        assert cmd.index(str(ruby)) == cmd.index("exec") + 1
        # And its directory leads PATH for nested lookups.
        assert env["PATH"].split(":")[0] == str(ruby.parent)

    def test_app_run_falls_back_to_homebrew_ruby(self, monkeypatch, clean_env, tmp_path):
        """An install predating ICLOUDBRIDGE_RUBY_PATH must not use system Ruby."""
        bundle = tmp_path / "bundle"
        bundle.write_text("#!/bin/sh\n")
        bundle.chmod(0o755)
        monkeypatch.setenv("ICLOUDBRIDGE_BUNDLE_PATH", str(bundle))

        cmd, _, _ = _build_ripper_command([])

        interpreter = cmd[cmd.index("exec") + 1]
        if Path("/opt/homebrew/opt/ruby/bin/ruby").exists():
            assert interpreter == "/opt/homebrew/opt/ruby/bin/ruby"
        assert interpreter != "/usr/bin/ruby"

    def test_dev_checkout_uses_path_ruby(self, clean_env):
        """Outside the app, the developer's own ruby is the right choice."""
        cmd, _, _ = _build_ripper_command([])

        assert cmd[cmd.index("exec") + 1] == "ruby"
