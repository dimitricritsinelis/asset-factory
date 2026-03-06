PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYPATH := PYTHONPATH="$(CURDIR)/asset_builder_code"
ARGS ?=

.PHONY: setup build smoke check

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

build:
	@test -n "$(SPEC)" || (echo "SPEC=asset_definitions/enemy_raider.yaml is required" && exit 2)
	$(PYPATH) $(PY) -m asset_factory build "$(SPEC)" $(ARGS)

smoke:
	$(PYPATH) $(PY) -m asset_factory build asset_definitions/enemy_raider.yaml --fixture-replay --force

check:
	$(PYPATH) $(PY) -m py_compile asset_builder_code/asset_factory/*.py
	$(PYPATH) $(PY) -m asset_factory --help
	$(MAKE) smoke
