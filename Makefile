IMAGE_BUILD_CMD ?= $(shell which podman 2>/dev/null || which docker)
IMAGE_TAG ?= latest

pre-commit:
	python3 -m pip install pip pre-commit --upgrade
	pre-commit run --all-files

tox:
	python3 -m pip install pip tox --upgrade
	tox

tests: pre-commit tox

install:
	python3 -m pip install pip poetry --upgrade
	poetry install

build-container:
	$(IMAGE_BUILD_CMD) build -t quay.io/redhat_msi/openshift-cli-installer:$(IMAGE_TAG) .

push-container: build-container
	$(IMAGE_BUILD_CMD) push quay.io/redhat_msi/openshift-cli-installer:$(IMAGE_TAG)

release:
	release-it
