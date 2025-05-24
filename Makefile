GO_CMD=go
GO_SRC=og
GO_OUT=build/og

PYTHON=.venv/bin/python
UV=uv

.PHONY: all build clean run go-run py-run venv

all: build

## Build the Go CLI
build:
	mkdir -p build
	$(GO_CMD) build -o $(GO_OUT) ./$(GO_SRC)

## Clean build artifacts
clean:
	rm -rf build
	rm -rf .venv

## Run the Go CLI
go-run: build
	./$(GO_OUT)

## Set up Python virtual environment using uv
venv:
	$(UV) init .
	$(UV) venv

## Run Python agent (entrypoint: agent.main:main)
py-run: venv
	source .venv/bin/activate && $(PYTHON) -m agent.main
