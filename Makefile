.DEFAULT_GOAL := help

MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
CURRENT_DIR := $(dir $(MKFILE_PATH))

IMAGE ?= mulby/py-opt-cli:snapshot

# Generates a help message. Borrowed from https://github.com/pydanny/cookiecutter-djangopackage.
help: ## Display this help message
	@echo "Please use \`make <target>' where <target> is one of"
	@perl -nle'print $& if m{^[\.a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m  %-25s\033[0m %s\n", $$1, $$2}'

build: ## Build a new version of the docker container
	docker build --no-cache -t $(IMAGE) .

update-requirements: ## Update the requirements.txt file to include the latest versions of each package
	docker run \
          -e LOCAL_USER_ID=$(shell id -u) \
          -v $(CURRENT_DIR):/workspace \
          $(IMAGE) \
          /bin/bash -c 'sudo pip install pip-tools; pip-compile --upgrade'

push: ## Push the built image up to dockerhub
	docker push $(IMAGE)
