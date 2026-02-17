.PHONY: help venv install clean run start stop status

help:
	@echo "tui-service (preview)"
	@echo ""
	@echo "Targets:"
	@echo "  make venv     - create .venv"
	@echo "  make install  - install requirements.txt"
	@echo "  make run      - run in foreground"
	@echo "  make start    - run in background (scripts/start.sh)"
	@echo "  make stop     - stop background process"
	@echo "  make status   - show status"

venv:
	python3 -m venv .venv

install:
	. .venv/bin/activate && pip install -q --upgrade pip && pip install -q -r requirements.txt

clean:
	rm -rf .venv __pycache__ src/__pycache__ tests/__pycache__ .pytest_cache

run:
	. .venv/bin/activate && python -m src

start:
	./scripts/start.sh run

stop:
	./scripts/start.sh stop

status:
	./scripts/start.sh status
