set shell := ["zsh", "-c"]

release args='':
	python3 scripts/build_release.py {{args}}

build:
	just release "--skip-dmg"
