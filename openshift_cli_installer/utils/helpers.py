import os
import shutil
from functools import wraps
from importlib.util import find_spec
from time import sleep

import click
import rosa.cli
import shortuuid
import yaml
from clouds.aws.session_clients import s3_client
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocm_python_wrapper.versions import Versions

from openshift_cli_installer.tests.all_rosa_versions import BASE_AVAILABLE_VERSIONS_DICT
from openshift_cli_installer.utils.cluster_versions import set_clusters_versions
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    CLUSTER_DATA_YAML_FILENAME,
    HYPERSHIFT_STR,
    ROSA_STR,
)


# TODO: Move to own repository.
def ignore_exceptions(logger=None, retry=None):
    def wrapper(func):
        @wraps(func)
        def inner(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                if retry:
                    for _ in range(0, retry):
                        try:
                            return func(*args, **kwargs)
                        except Exception:
                            sleep(1)

                if logger:
                    logger.info(ex)
                return None

        return inner

    return wrapper


def remove_terraform_folder_from_install_dir(install_dir):
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


def get_ocm_client(ocm_token, ocm_env):
    return OCMPythonClient(
        token=ocm_token,
        endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        api_host=ocm_env,
        discard_unknown_keys=True,
    ).client


def cluster_shortuuid():
    return shortuuid.uuid()


@ignore_exceptions()
def zip_and_upload_to_s3(
    install_dir,
    s3_bucket_name,
    uuid,
    s3_bucket_path=None,
):
    remove_terraform_folder_from_install_dir(install_dir=install_dir)

    _base_name = f"{install_dir}-{uuid}"

    zip_file = shutil.make_archive(
        base_name=_base_name,
        format="zip",
        root_dir=install_dir,
    )
    bucket_key = os.path.join(s3_bucket_path or "", os.path.split(zip_file)[-1])
    click.echo(f"Upload {zip_file} file to S3 {s3_bucket_name}, path {bucket_key}")
    s3_client().upload_file(
        Filename=zip_file,
        Bucket=s3_bucket_name,
        Key=bucket_key,
    )

    return _base_name


def dump_cluster_data_to_file(cluster_data):
    with open(
        os.path.join(cluster_data["install-dir"], CLUSTER_DATA_YAML_FILENAME), "w"
    ) as fd:
        fd.write(yaml.dump(cluster_data))


def bucket_object_name(cluster_data, _shortuuid, s3_bucket_path=None):
    return (
        f"{f'{s3_bucket_path}/' if s3_bucket_path else ''}{cluster_data['name']}-{_shortuuid}.zip"
    )


def get_manifests_path():
    manifests_path = os.path.join("openshift_cli_installer", "manifests")
    if not os.path.isdir(manifests_path):
        manifests_path = os.path.join(
            find_spec("openshift_cli_installer").submodule_search_locations[0],
            "manifests",
        )
    return manifests_path


def update_rosa_osd_clusters_versions(clusters, ocm_env, ocm_token, _test=False):
    if _test:
        base_available_versions_dict = BASE_AVAILABLE_VERSIONS_DICT
    else:
        base_available_versions_dict = {}
        for cluster_data in clusters:
            if cluster_data["platform"] == AWS_OSD_STR:
                base_available_versions_dict = Versions(
                    client=cluster_data["ocm-client"]
                ).get(channel_group=cluster_data["channel-group"])

            elif cluster_data["platform"] in (ROSA_STR, HYPERSHIFT_STR):
                channel_group = cluster_data["channel-group"]
                base_available_versions = rosa.cli.execute(
                    command=(
                        f"list versions --channel-group={channel_group} "
                        f"{'--hosted-cp' if cluster_data['platform'] == HYPERSHIFT_STR else ''}"
                    ),
                    aws_region=cluster_data["region"],
                    ocm_env=ocm_env,
                    token=ocm_token,
                )["out"]
                _all_versions = [ver["raw_id"] for ver in base_available_versions]
                base_available_versions_dict[channel_group] = _all_versions

    return set_clusters_versions(
        clusters=clusters,
        base_available_versions=base_available_versions_dict,
    )
