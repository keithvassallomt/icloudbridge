#!/usr/bin/env python3
"""
Build the distributable macOS app bundle plus DMG.

Steps:
1. Ensure Poetry dependencies (main group) are installed.
2. Build the frontend via Vite.
3. Build the menubar Swift app via swift build.
4. Build the backend executable via Briefcase (unless overridden).
5. Assemble `build/Release/iCloudBridge.app` with binaries + assets.
6. Create a compressed DMG for distribution.
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
BRIEFCASE_APP_PATH = (
    ROOT / "build" / "backend" / "macos" / "app" / "iCloudBridgeBackend.app"
)
DMGCANVAS_TEMPLATE = ROOT / "assets" / "icb_dmg_canvas.dmgcanvas"

# Code signing constants
DEVELOPER_ID = "Developer ID Application: Keith Vassallo (W4SF9AYV8T)"
NOTARYTOOL_PROFILE = "notarytool-profile"


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    location = cwd or ROOT
    print(f"→ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(location), check=True, env=env)


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


def copy_binary(src: Path, dest: Path) -> None:
    shutil.copy2(src, dest)
    dest.chmod(0o755)


def build_backend_with_briefcase() -> Path:
    env = os.environ.copy()

    def briefcase_cmd(subcommand: str) -> None:
        run(
            ["poetry", "run", "briefcase", subcommand, "macOS", "app", "-a", "backend", "--no-input"],
            env=env,
        )

    briefcase_cmd("create")
    briefcase_cmd("update")
    briefcase_cmd("build")

    if not BRIEFCASE_APP_PATH.exists():
        raise FileNotFoundError(f"Briefcase build did not produce app at {BRIEFCASE_APP_PATH}")
    return BRIEFCASE_APP_PATH


def stage_app_bundle(version: str, menubar_binary: Path, backend_app_path: Path) -> None:
    contents = APP_ROOT / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    frameworks_dir = contents / "Frameworks"
    resources_dir.mkdir(parents=True, exist_ok=True)
    frameworks_dir.mkdir(parents=True, exist_ok=True)
    backend_contents = backend_app_path / "Contents"
    backend_macos = backend_contents / "MacOS"
    backend_resources = backend_contents / "Resources"
    backend_frameworks = backend_contents / "Frameworks"

    backend_info_path = backend_contents / "Info.plist"
    backend_info = plistlib.loads(backend_info_path.read_bytes())
    template_info = plistlib.loads(INFO_PLIST_TEMPLATE.read_bytes())
    template_info["CFBundleShortVersionString"] = version
    template_info["CFBundleVersion"] = version
    if main_module := backend_info.get("MainModule"):
        template_info["MainModule"] = main_module
    plist_path = contents / "Info.plist"
    with plist_path.open("wb") as plist_file:
        plistlib.dump(template_info, plist_file)
    app_icon_source = ROOT / "macos" / "AppBundle" / "AppIcon.icns"
    if app_icon_source.exists():
        shutil.copy2(app_icon_source, resources_dir / "AppIcon.icns")

    # Sync backend runtime payload
    backend_binary_src = backend_macos / "iCloudBridgeBackend"
    backend_binary_dest = macos_dir / "icloudbridge-backend"
    copy_binary(backend_binary_src, backend_binary_dest)

    if backend_frameworks.exists():
        for item in backend_frameworks.iterdir():
            dest = frameworks_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

    if backend_resources.exists():
        for item in backend_resources.iterdir():
            dest = resources_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    copy_binary(menubar_binary, macos_dir / "iCloudBridgeMenubar")

    # Copy menubar resources (icons) from bundle to Resources directory
    menubar_bundle = menubar_binary.parent / "iCloudBridgeMenubar_iCloudBridgeMenubar.bundle"
    if menubar_bundle.exists():
        for resource in menubar_bundle.iterdir():
            if resource.is_file():
                shutil.copy2(resource, resources_dir / resource.name)

    public_dir = resources_dir / "public"
    if public_dir.exists():
        shutil.rmtree(public_dir)
    shutil.copytree(FRONTEND_DIR / "dist", public_dir)


def sign_app_bundle(production: bool = False) -> None:
    """Sign the complete app bundle after assembly.

    Args:
        production: If True, use Developer ID certificate with hardened runtime.
                   If False, use ad-hoc signing for development.
    """
    sign_identity = DEVELOPER_ID if production else "-"
    print(f"→ Signing app bundle ({'production' if production else 'debug'})")

    # Build base codesign arguments
    base_args = ["codesign", "--force", "--sign", sign_identity]

    # Add hardened runtime and entitlements for production builds
    if production:
        base_args.extend([
            "--options", "runtime",
            "--entitlements", str(ENTITLEMENTS_FILE),
            "--timestamp"
        ])

    # Sign frameworks first (from deepest to shallowest)
    frameworks_dir = APP_ROOT / "Contents" / "Frameworks"
    if frameworks_dir.exists():
        for framework in frameworks_dir.rglob("*.framework"):
            if framework.is_dir():
                print(f"  Signing {framework.name}")
                args = base_args.copy()
                args.append(str(framework))
                subprocess.run(
                    args,
                    stderr=subprocess.DEVNULL,  # Suppress warnings about ambiguous formats
                    check=False  # Don't fail on framework signing errors
                )

        for dylib in frameworks_dir.rglob("*.dylib"):
            if dylib.is_file():
                args = base_args.copy()
                args.append(str(dylib))
                subprocess.run(
                    args,
                    stderr=subprocess.DEVNULL,
                    check=False
                )

    # Sign all executables in MacOS directory
    macos_dir = APP_ROOT / "Contents" / "MacOS"
    for binary in macos_dir.iterdir():
        if binary.is_file() and binary.stat().st_mode & 0o111:
            print(f"  Signing {binary.name}")
            args = base_args.copy()
            args.append(str(binary))
            run(args)

    # Sign the main app bundle
    args = base_args.copy()
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
        # DMGCanvas command-line tool expects: dmgcanvas <template> <output> -app <app-bundle>
        run([
            "dmgcanvas",
            str(DMGCANVAS_TEMPLATE),
            str(dmg),
            "-app", str(APP_ROOT)
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
    parser.add_argument(
        "--backend-app",
        type=Path,
        help="Path to a Briefcase-generated backend .app bundle to embed",
        default=os.environ.get("ICLOUDBRIDGE_BACKEND_APP"),
    )
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
    backend_app: Path
    if args.backend_app:
        backend_path = Path(args.backend_app).expanduser()
        if not backend_path.exists():
            raise FileNotFoundError(f"Backend app not found at {backend_path}")
        backend_app = backend_path.resolve()
    else:
        backend_app = build_backend_with_briefcase()
    stage_app_bundle(version, menubar_binary, backend_app)
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
