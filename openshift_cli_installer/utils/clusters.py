import contextlib
import copy
import os
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import yaml
from clouds.aws.session_clients import s3_client
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocp_utilities.utils import run_command
from simple_logger.logger import get_logger

from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY,
    DESTROY_STR,
)

LOGGER = get_logger(__name__)


def get_ocm_client(ocm_token, ocm_env):
    return OCMPythonClient(
        token=ocm_token,
        endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        api_host=ocm_env,
        discard_unknown_keys=True,
    ).client


@contextlib.contextmanager
def get_kubeadmin_token(cluster_dir, api_url):
    with open(os.path.join(cluster_dir, "auth", "kubeadmin-password")) as fd:
        kubeadmin_password = fd.read()
    run_command(
        shlex.split(
            f"oc login --insecure-skip-tls-verify=true {api_url} -u kubeadmin -p"
            f" {kubeadmin_password}"
        ),
        hide_log_command=True,
    )
    yield run_command(
        shlex.split("oc whoami -t"),
        hide_log_command=True,
    )[1].strip()
    run_command(shlex.split("oc logout"))


def clusters_from_directories(directories):
    clusters_data_list = []
    for directory in directories:
        for root, dirs, files in os.walk(directory):
            for _file in files:
                if _file == CLUSTER_DATA_YAML_FILENAME:
                    with open(os.path.join(root, _file)) as fd:
                        _data = yaml.safe_load(fd)

                    clusters_data_list.append(_data)

    return clusters_data_list


def get_destroy_clusters_kwargs(clusters_data_list, **kwargs):
    kwargs["action"] = DESTROY_STR

    for cluster_data_from_yaml in clusters_data_list:
        cluster_data_from_yaml["cluster"].pop("expiration-time", None)
        kwargs.setdefault("clusters", []).append(cluster_data_from_yaml["cluster"])

    return kwargs


def prepare_clusters_directory_from_s3_bucket(**kwargs):
    s3_bucket_name = kwargs["s3_bucket_name"]
    s3_bucket_path = kwargs["s3_bucket_path"]
    download_futures = []
    extract_futures = []
    target_files_paths = []
    _s3_client = s3_client()
    for cluster_zip_file in get_all_zip_files_from_s3_bucket(
        client=_s3_client,
        s3_bucket_name=s3_bucket_name,
        s3_bucket_path=s3_bucket_path,
        query=kwargs["destroy_clusters_from_s3_bucket_query"],
    ):
        extract_target_dir = os.path.join(
            DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY,
            cluster_zip_file.split(".")[0],
        )
        Path(extract_target_dir).mkdir(parents=True, exist_ok=True)
        target_file_path = os.path.join(extract_target_dir, cluster_zip_file)
        cluster_zip_path = os.path.join(kwargs["s3_bucket_path"], cluster_zip_file)
        with ThreadPoolExecutor() as download_executor:
            download_futures.append(
                download_executor.submit(
                    _s3_client.download_file(
                        Bucket=kwargs["s3_bucket_name"],
                        Key=cluster_zip_path,
                        Filename=target_file_path,
                    )
                )
            )
            target_files_paths.append(target_file_path)

        if download_futures:
            for _ in as_completed(download_futures):
                """
                Place holder to make sure all futures are completed.
                """

    for zip_file_path in target_files_paths:
        with ThreadPoolExecutor() as extract_executor:
            extract_futures.append(
                extract_executor.submit(
                    shutil.unpack_archive(
                        filename=zip_file_path,
                        extract_dir=os.path.split(zip_file_path)[0],
                        format="zip",
                    )
                )
            )

        if extract_futures:
            for _ in as_completed(extract_futures):
                """
                Place holder to make sure all futures are completed.
                """

    return clusters_from_directories(
        directories=[DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY]
    )


def get_all_zip_files_from_s3_bucket(
    client, s3_bucket_name, s3_bucket_path=None, query=None
):
    for _object in client.list_objects(
        Bucket=s3_bucket_name, Prefix=s3_bucket_path
    ).get("Contents", []):
        _object_key = _object["Key"]
        if _object_key.endswith(".zip"):
            if query is None or query in _object_key:
                yield os.path.split(_object_key)[-1]


def destroy_clusters_from_s3_bucket_or_local_directory(**kwargs):
    s3_clusters_data_list = []
    data_directory_clusters_data_list = []

    s3_from_clusters_data_directory = kwargs[
        "destroy_clusters_from_install_data_directory_using_s3_bucket"
    ]
    destroy_clusters_from_install_data_directory = kwargs[
        "destroy_clusters_from_install_data_directory"
    ]
    if kwargs["destroy_clusters_from_s3_bucket"]:
        s3_clusters_data_list.extend(
            prepare_clusters_directory_from_s3_bucket(**kwargs)
        )

    if destroy_clusters_from_install_data_directory or s3_from_clusters_data_directory:
        clusters_from_directory = clusters_from_directories(
            directories=[kwargs["clusters_install_data_directory"]]
        )
        if destroy_clusters_from_install_data_directory:
            data_directory_clusters_data_list.extend(clusters_from_directory)

        elif s3_from_clusters_data_directory:
            copy_kwargs = copy.deepcopy(kwargs)
            for _cluster in clusters_from_directory:
                _s3_object_name = _cluster.get("s3_object_name")
                _s3_bucket_name = _cluster.get("s3_bucket_name")
                if _s3_object_name and _s3_bucket_name:
                    copy_kwargs["s3_bucket_name"] = _s3_bucket_name
                    copy_kwargs["s3_bucket_path"] = _cluster.get("s3_bucket_path")
                    copy_kwargs["query"] = os.path.split(_s3_object_name)[-1]
                    s3_clusters_data_list.extend(
                        prepare_clusters_directory_from_s3_bucket(**copy_kwargs)
                    )

    clusters_kwargs = get_destroy_clusters_kwargs(
        clusters_data_list=s3_clusters_data_list + data_directory_clusters_data_list,
        **kwargs,
    )
    if not clusters_kwargs.get("clusters"):
        LOGGER.error("No clusters to destroy")
        raise click.Abort()

    return clusters_kwargs
