.PHONY: app dmg update native native-test test legacy-test check

app:
	bash scripts/build_app.sh

dmg:
	bash scripts/build_dmg.sh

update:
	python3 scripts/release_updates.py build $(if $(RELEASE_TAG),--tag $(RELEASE_TAG),)

native:
	bash scripts/build_native.sh

native-test:
	bash scripts/build_native.sh test

test: native-test
	bash scripts/test_update_gate.sh
	bash scripts/test_coreml_evidence.sh

legacy-test:
	.venv/bin/python -m pytest -q

check: test
	for script in scripts/*.sh; do bash -n "$$script"; done
	plutil -lint packaging/Info.plist
	swift build --build-system native -c release
