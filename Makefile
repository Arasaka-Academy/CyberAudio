.PHONY: app dmg update native native-test test tools-test legacy-test check

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
	bash scripts/test_preparation.sh
	bash scripts/test_update_gate.sh
	bash scripts/test_coreml_evidence.sh
	bash scripts/test_dictation.sh
	bash scripts/test_audio_workflow.sh
	bash scripts/test_model_management.sh
	bash scripts/build_ui.sh
	bash scripts/test_workspace_layout.sh

legacy-test:
	.venv/bin/python -m pytest backend/ -q

tools-test:
	bash scripts/test_tools.sh

check: test tools-test
	bash scripts/build_native.sh
	for script in scripts/*.sh; do bash -n "$$script"; done
	plutil -lint packaging/Info.plist
