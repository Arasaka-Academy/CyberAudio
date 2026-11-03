.PHONY: app test check

app:
	bash scripts/build_app.sh

test:
	.venv/bin/python -m pytest -q

check: test
	.venv/bin/python -m compileall -q backend scripts tests
	bash -n scripts/build_app.sh
	plutil -lint packaging/Info.plist
	swift build -c release
