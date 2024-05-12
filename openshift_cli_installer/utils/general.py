from __future__ import annotations
import json
import os
import shutil
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict

import click
import yaml
from clouds.aws.session_clients import s3_client
from jinja2 import DebugUndefined, Environment, FileSystemLoader, meta
from pyhelper_utils.general import ignore_exceptions
from simple_logger.logger import get_logger


LOGGER = get_logger(name=__name__)


def remove_terraform_folder_from_install_dir(install_dir: str) -> None:
    """
    .terraform folder created when call terraform.init() and it's take more space.
    """
    folders_to_remove = []
    for root, dirs, files in os.walk(install_dir):
        for _dir in dirs:
            if _dir == ".terraform":
                folders_to_remove.append(os.path.join(root, _dir))

    for folder in folders_to_remove:
        shutil.rmtree(folder)


@ignore_exceptions(logger=LOGGER)
def zip_and_upload_to_s3(install_dir: str, s3_bucket_name: str, s3_bucket_object_name: str) -> None:
    remove_terraform_folder_from_install_dir(install_dir=install_dir)

    _base_name = os.path.join(Path(install_dir).parent, Path(s3_bucket_object_name).stem)
    LOGGER.info(f"Writing data from {install_dir} to {_base_name} zip file")
    zip_file = shutil.make_archive(base_name=_base_name, format="zip", root_dir=install_dir)

    LOGGER.info(f"Upload {zip_file} file to S3 {s3_bucket_name}, path {s3_bucket_object_name}")
    s3_client().upload_file(Filename=zip_file, Bucket=s3_bucket_name, Key=s3_bucket_object_name)


def get_manifests_path() -> str:
    manifests_path = os.path.join("openshift_cli_installer", "manifests")
    if not os.path.isdir(manifests_path):
        manifests_path = os.path.join(
            find_spec("openshift_cli_installer").submodule_search_locations[0],  # type: ignore
            "manifests",
        )
    return manifests_path


def get_install_config_j2_template(jinja_dict: Dict[str, str], platform: str) -> Dict[str, Any]:
    env = Environment(
        loader=FileSystemLoader(get_manifests_path()),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=DebugUndefined,
    )

    template = env.get_template(name=f"{platform}-install-config-template.j2")
    rendered = template.render(jinja_dict)
    undefined_variables = meta.find_undeclared_variables(env.parse(rendered))
    if undefined_variables:
        LOGGER.error(f"The following variables are undefined: {undefined_variables}")
        raise click.Abort()

    return yaml.safe_load(rendered)


def generate_unified_pull_secret(registry_config_file: str, docker_config_file: str) -> str:
    registry_config = get_pull_secret_data(registry_config_file=registry_config_file)
    docker_config = get_pull_secret_data(registry_config_file=docker_config_file)
    docker_config["auths"].update(registry_config["auths"])

    return json.dumps(docker_config)


def get_pull_secret_data(registry_config_file: str) -> Dict[str, Any]:
    with open(registry_config_file) as fd:
        return json.load(fd)


def get_local_ssh_key(ssh_key_file: str) -> str:
    with open(ssh_key_file) as fd:
        return fd.read().strip()


def get_dict_from_json(gcp_service_account_file: str) -> Dict[str, Any]:
    with open(gcp_service_account_file) as fd:
        return json.loads(fd.read())
