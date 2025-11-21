set shell := ["zsh", "-c"]

# Install dependencies
install:
	poetry install
	cd frontend && npm install
	cd tools/notes_cloud_ripper && bundle install

# Install only main dependencies (no dev tools)
install-main:
	poetry install --only main
	cd frontend && npm install
	cd tools/notes_cloud_ripper && bundle install

# Clean all build artifacts
clean:
	rm -rf build/ dist/
	rm -f *.dmg 2>/dev/null || true
	rm -rf frontend/dist frontend/node_modules/.cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Build PyInstaller backend only
build-backend:
	poetry run pyinstaller --clean --noconfirm icloudbridge-backend.spec

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

# Run the development server
dev:
	poetry run dev-server

# Run tests
test:
	poetry run pytest

# Run linter
lint:
	poetry run ruff check .

# Format code
format:
	poetry run ruff format .

# Verify code signing of built app
verify-signing:
	codesign -vvv --deep --strict build/Release/iCloudBridge.app
	spctl -a -vvv build/Release/iCloudBridge.app
       
