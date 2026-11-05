.PHONY: app dmg update test check

app:
	bash scripts/build_app.sh

dmg:
	bash scripts/build_dmg.sh

update:
	python3 scripts/release_updates.py build $(if $(RELEASE_TAG),--tag $(RELEASE_TAG),)

test:
	.venv/bin/python -m pytest -q
	bash scripts/test_update_gate.sh

check: test
	.venv/bin/python -m compileall -q backend scripts tests
	for script in scripts/*.sh; do bash -n "$$script"; done
	plutil -lint packaging/Info.plist
	swift build -c release
