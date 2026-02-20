PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
ARGS ?=

.PHONY: setup smoke build build-all clean-temp

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

smoke:
	$(PY) -m asset_factory build assets_pipeline/tests/smoke_cube.yaml --skip-openai --skip-images --force

build:
	@test -n "$(NAME)" || (echo "Usage: make build NAME=<spec_name>" && exit 1)
	$(PY) -m asset_factory build $(NAME) $(ARGS)

build-all:
	$(PY) -m asset_factory build-all $(ARGS)

clean-temp:
	rm -f assets/models/tests/_*.glb
	rm -f assets/models/tests/*_seltest*.glb
	rm -f assets/models/tests/*_norm*.glb
	rm -f assets/reports/tests/_*.json
	rm -f assets/reports/tests/*_seltest*.json
	rm -f assets/reports/tests/*_norm*.json
	rm -f assets/thumbnails/tests/_*.png
	rm -f assets/thumbnails/tests/*_seltest*.png
	rm -f assets/thumbnails/tests/*_norm*.png
