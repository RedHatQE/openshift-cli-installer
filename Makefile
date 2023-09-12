IMAGE_BUILD_CMD ?= $(shell which podman 2>/dev/null || which docker)
IMAGE_TAG ?= latest

test:
	pre-commit run --all-files
	tox

install:
	python3 -m pip install pip poetry --upgrade
	poetry install

build-container:
	$(IMAGE_BUILD_CMD) build -t quay.io/redhat_msi/openshift-cli-installe:$(IMAGE_TAG) .

push-container: build-container
	$(IMAGE_BUILD_CMD) push quay.io/redhat_msi/openshift-cli-installe:$(IMAGE_TAG)

release:
	release-it
