.PHONY: run test lint demo

run:
	PYTHONPATH=src python3 -m notification_service.main all

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

lint:
	python3 -m compileall -q src tests

demo:
	./scripts/demo.sh

