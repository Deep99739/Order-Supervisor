.PHONY: setup start stop

setup:
	@./scripts/setup-local.sh

start:
	@./scripts/start-local.sh

stop:
	@./scripts/docker-compose.sh down
