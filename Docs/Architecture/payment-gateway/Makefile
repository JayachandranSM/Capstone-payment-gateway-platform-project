# Makefile — operator shortcuts. Default = `podman-compose`; override with
# `make up COMPOSE=docker compose` if you want to run under Docker.
COMPOSE ?= podman-compose

.PHONY: help up down build logs ps health restart shell-core shell-ai clean

help:               ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:                 ## Build + start the whole stack in the background
	$(COMPOSE) up -d --build

down:               ## Stop and remove containers (volumes preserved)
	$(COMPOSE) down

build:              ## Rebuild all images
	$(COMPOSE) build

logs:               ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

ps:                 ## Show service status
	$(COMPOSE) ps

health:             ## Hit each /healthz endpoint
	@echo "core-api:"   && curl -sf http://localhost:$${CORE_API_PORT:-8000}/healthz   && echo
	@echo "ai-service:" && curl -sf http://localhost:$${AI_SERVICE_PORT:-8100}/healthz && echo
	@echo "frontend:"   && curl -sfo /dev/null -w "  HTTP %{http_code}\n" http://localhost:$${FRONTEND_PORT:-3000}/

restart:            ## Restart all services
	$(COMPOSE) restart

shell-core:         ## Shell inside core-api
	$(COMPOSE) exec core-api /bin/bash

shell-ai:           ## Shell inside ai-service
	$(COMPOSE) exec ai-service /bin/bash

clean:              ## Stop + remove containers AND named volumes
	$(COMPOSE) down -v
