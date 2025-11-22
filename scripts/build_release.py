#!/usr/bin/env python3
"""
Build the distributable macOS app bundle plus DMG.

Steps:
1. Ensure Poetry dependencies (main group) are installed.
2. Build the frontend via Vite.
3. Build the menubar Swift app via swift build.
4. Assemble `build/Release/iCloudBridge.app` with binaries + assets (including backend source + deps).
5. Create a compressed DMG for distribution.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
RELEASE_ROOT = BUILD_DIR / "Release"
APP_NAME = "iCloudBridge.app"
APP_ROOT = RELEASE_ROOT / APP_NAME
INFO_PLIST_TEMPLATE = ROOT / "macos" / "AppBundle" / "Info.plist"
ENTITLEMENTS_FILE = ROOT / "macos" / "AppBundle" / "Entitlements.plist"
FRONTEND_DIR = ROOT / "frontend"
MENUBAR_DIR = ROOT / "macos" / "MenubarApp"
DMGCANVAS_TEMPLATE = ROOT / "icb_dmg_canvas.dmgcanvas"

# Code signing constants
DEVELOPER_ID = "Developer ID Application: Keith Vassallo (W4SF9AYV8T)"
NOTARYTOOL_PROFILE = "notarytool-profile"


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    location = cwd or ROOT
    print(f"→ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(location), check=True, env=env)


def is_macho_binary(path: Path) -> bool:
    """Check if a file is a Mach-O binary (not a script or text file)."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            # Mach-O magic numbers
            return magic in (
                b"\xfe\xed\xfa\xce",  # 32-bit
                b"\xfe\xed\xfa\xcf",  # 64-bit
                b"\xca\xfe\xba\xbe",  # Universal/Fat binary
                b"\xce\xfa\xed\xfe",  # 32-bit reverse
                b"\xcf\xfa\xed\xfe",  # 64-bit reverse
            )
    except Exception:
        return False


def read_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return data["tool"]["poetry"]["version"]


def clean_release_dir() -> None:
    if RELEASE_ROOT.exists():
        shutil.rmtree(RELEASE_ROOT, ignore_errors=True)
    (APP_ROOT / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (APP_ROOT / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)


def install_poetry_dependencies() -> None:
    run(["poetry", "install", "--only", "main"])


def build_frontend() -> None:
    run(["npm", "--prefix", str(FRONTEND_DIR), "ci"])
    run(["npm", "--prefix", str(FRONTEND_DIR), "run", "build"])


def build_menubar_binary() -> Path:
    run(["swift", "build", "-c", "release"], cwd=MENUBAR_DIR)
    binary = MENUBAR_DIR / ".build" / "release" / "iCloudBridgeMenubar"
    if not binary.exists():
        raise FileNotFoundError(f"Menubar binary not found at {binary}")
    return binary


def login_helper_binary_path() -> Path:
    return MENUBAR_DIR / ".build" / "release" / "iCloudBridgeLoginHelper"


def copy_binary(src: Path, dest: Path) -> None:
    shutil.copy2(src, dest)
    dest.chmod(0o755)


def stage_app_bundle(version: str, menubar_binary: Path) -> None:
    """Stage the app bundle with resources and menubar binary."""
    contents = APP_ROOT / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    # Create Info.plist
    template_info = plistlib.loads(INFO_PLIST_TEMPLATE.read_bytes())
    template_info["CFBundleShortVersionString"] = version
    template_info["CFBundleVersion"] = version
    plist_path = contents / "Info.plist"
    with plist_path.open("wb") as plist_file:
        plistlib.dump(template_info, plist_file)

    # Copy app icon
    app_icon_source = ROOT / "macos" / "AppBundle" / "AppIcon.icns"
    if app_icon_source.exists():
        shutil.copy2(app_icon_source, resources_dir / "AppIcon.icns")

    # Copy menubar binary (main executable)
    copy_binary(menubar_binary, macos_dir / "iCloudBridgeMenubar")

    # Copy menubar resources (icons) from bundle to Resources directory
    menubar_bundle = menubar_binary.parent / "iCloudBridgeMenubar_iCloudBridgeMenubar.bundle"
    if menubar_bundle.exists():
        for resource in menubar_bundle.iterdir():
            if resource.is_file():
                shutil.copy2(resource, resources_dir / resource.name)

    # Copy frontend build
    public_dir = resources_dir / "public"
    if public_dir.exists():
        shutil.rmtree(public_dir)
    shutil.copytree(FRONTEND_DIR / "dist", public_dir)

    # Copy backend source and dependency locks into Resources
    backend_src_dir = resources_dir / "backend_src"
    if backend_src_dir.exists():
        shutil.rmtree(backend_src_dir)
    backend_src_dir.mkdir(parents=True, exist_ok=True)

    for path in [ROOT / "backend", ROOT / "icloudbridge", ROOT / "pyproject.toml", ROOT / "requirements.lock", ROOT / "README.md"]:
        dest = backend_src_dir / path.name
        if path.is_dir():
            shutil.copytree(path, dest)
        elif path.is_file():
            shutil.copy2(path, dest)

    ruby_dir = resources_dir / "ruby_deps"
    ruby_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools" / "notes_cloud_ripper" / "Gemfile", ruby_dir / "Gemfile")
    shutil.copy2(ROOT / "tools" / "notes_cloud_ripper" / "Gemfile.lock", ruby_dir / "Gemfile.lock")

    # Build login helper app bundle into LoginItems so SMAppService can expose a nice name/icon in System Settings
    helper_bin = login_helper_binary_path()
    login_items_dir = contents / "Library" / "LoginItems"
    helper_app_dir = login_items_dir / "iCloudBridgeLoginHelper.app"
    if helper_bin.exists():
        helper_macos_dir = helper_app_dir / "Contents" / "MacOS"
        helper_macos_dir.mkdir(parents=True, exist_ok=True)
        dest_bin = helper_macos_dir / helper_bin.name
        shutil.copy2(helper_bin, dest_bin)
        dest_bin.chmod(0o755)

        # Write helper Info.plist
        helper_plist_src = MENUBAR_DIR / "Sources" / "LoginItemHelper" / "Info.plist"
        helper_plist_dst = helper_app_dir / "Contents" / "Info.plist"
        helper_plist_dst.parent.mkdir(parents=True, exist_ok=True)
        if helper_plist_src.exists():
            shutil.copy2(helper_plist_src, helper_plist_dst)
            try:
                plist_data = plistlib.loads(helper_plist_dst.read_bytes())
                plist_data["CFBundleShortVersionString"] = version
                plist_data["CFBundleVersion"] = version
                with helper_plist_dst.open("wb") as fh:
                    plistlib.dump(plist_data, fh)
            except Exception as exc:
                print(f"WARNING: could not update helper Info.plist: {exc}")
    else:
        print("WARNING: login helper binary not found; Background Items entry may be generic.")


def sign_app_bundle(production: bool = False) -> None:
    """Sign the app bundle (menubar binary; resources are data only)."""
    sign_identity = DEVELOPER_ID if production else "-"
    print(f"→ Signing app bundle ({'production' if production else 'debug'})")

    # 1. Sign menubar executable in MacOS (with hardened runtime if production)
    menubar_binary = APP_ROOT / "Contents" / "MacOS" / "iCloudBridgeMenubar"
    if menubar_binary.exists():
        print(f"  Signing {menubar_binary.name}")
        args = ["codesign", "--force", "--sign", sign_identity]
        if production:
            args.extend(["--options", "runtime", "--timestamp"])
        args.append(str(menubar_binary))
        run(args)

    # 3. Sign login helper app if present (needed for SMAppService)
    helper_app = APP_ROOT / "Contents" / "Library" / "LoginItems" / "iCloudBridgeLoginHelper.app"
    if helper_app.exists():
        print(f"  Signing login helper {helper_app.name}")
        helper_args = ["codesign", "--force", "--sign", sign_identity]
        if production:
            helper_args.extend(["--options", "runtime", "--timestamp"])
        helper_args.append(str(helper_app))
        run(helper_args)

    # 4. Sign outer app bundle last
    print(f"  Signing {APP_ROOT.name}")
    args = ["codesign", "--force", "--sign", sign_identity]
    if production:
        args.extend([
            "--options", "runtime",
            "--entitlements", str(ENTITLEMENTS_FILE),
            "--timestamp"
        ])
    args.append(str(APP_ROOT))
    run(args)


def create_dmg(use_dmgcanvas: bool = False) -> Path:
    """Create a DMG for distribution.

    Args:
        use_dmgcanvas: If True, use DMGCanvas for a fancy DMG. If False, use hdiutil for basic DMG.
    """
    dmg = RELEASE_ROOT / "iCloudBridge.dmg"
    if dmg.exists():
        dmg.unlink()

    if use_dmgcanvas:
        if not DMGCANVAS_TEMPLATE.exists():
            raise FileNotFoundError(f"DMGCanvas template not found at {DMGCANVAS_TEMPLATE}")

        print("→ Creating DMG with DMGCanvas")
        run([
            "dmgcanvas",
            str(DMGCANVAS_TEMPLATE),
            str(dmg)
        ])
    else:
        print("→ Creating DMG with hdiutil")
        run([
            "hdiutil",
            "create",
            "-volname",
            "iCloudBridge",
            "-srcfolder",
            str(APP_ROOT),
            "-ov",
            "-format",
            "UDZO",
            str(dmg),
        ])

    return dmg


def notarize_dmg(dmg_path: Path) -> None:
    """Notarize the DMG with Apple's notary service.

    This will:
    1. Submit the DMG to Apple for notarization
    2. Wait for the notarization to complete
    3. Staple the notarization ticket to the DMG
    """
    print("→ Submitting DMG for notarization (this may take a few minutes)")

    # Submit for notarization
    run([
        "xcrun", "notarytool", "submit",
        str(dmg_path),
        "--keychain-profile", NOTARYTOOL_PROFILE,
        "--wait"
    ])

    # Staple the notarization ticket to the DMG
    print("→ Stapling notarization ticket")
    run([
        "xcrun", "stapler", "staple",
        str(dmg_path)
    ])

    print("✓ Notarization complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-dmg", action="store_true", help="Skip DMG creation")
    parser.add_argument("--production", action="store_true", help="Use production code signing and notarization")
    parser.add_argument("--notarize", action="store_true", help="Notarize the DMG (requires --production)")
    args = parser.parse_args()

    # Validate arguments
    if args.notarize and not args.production:
        parser.error("--notarize requires --production")
    if args.notarize and args.skip_dmg:
        parser.error("--notarize requires DMG creation (cannot use --skip-dmg)")

    version = read_version()
    clean_release_dir()
    install_poetry_dependencies()
    build_frontend()
    menubar_binary = build_menubar_binary()
    stage_app_bundle(version, menubar_binary)
    sign_app_bundle(production=args.production)

    dmg_path = None
    if not args.skip_dmg:
        dmg_path = create_dmg(use_dmgcanvas=args.production)

        if args.notarize and dmg_path:
            notarize_dmg(dmg_path)

    print("\nBuild complete:")
    print(f"  App bundle: {APP_ROOT}")
    if dmg_path:
        print(f"  DMG: {dmg_path}")
        if args.notarize:
            print("  ✓ Notarized and stapled")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        sys.exit(exc.returncode)
    except FileNotFoundError as exc:
        print(f"Required tool is missing: {exc}", file=sys.stderr)
        sys.exit(1)
