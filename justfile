set shell := ["zsh", "-c"]

# Build app bundle only (debug/ad-hoc signed)
build-debug:
	python3 scripts/build_release.py --skip-dmg

# Build app bundle + DMG (debug/ad-hoc signed)
release-debug:
	python3 scripts/build_release.py

# Build app bundle only (production signed with Developer ID)
build:
	python3 scripts/build_release.py --production --skip-dmg

# Build app bundle + DMG with notarization (production signed)
release:
	python3 scripts/build_release.py --production --notarize
