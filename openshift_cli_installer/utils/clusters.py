from __future__ import annotations
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, List

import botocore
import click
import yaml
from clouds.aws.session_clients import s3_client
from ocm_python_client.api.default_api import DefaultApi
from ocm_python_wrapper.ocm_client import OCMPythonClient
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY,
    DESTROY_STR,
)

LOGGER = get_logger(name=__name__)


def get_ocm_client(ocm_token: str, ocm_env: str) -> DefaultApi:
    return OCMPythonClient(
        token=ocm_token,
        endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        api_host=ocm_env,
        discard_unknown_keys=True,
    ).client


def clusters_from_directories(directories: List[str]) -> List[Dict[str, Any]]:
    clusters_data_list = []
    for directory in directories:
        for root, dirs, files in os.walk(directory):
            for _file in files:
                if _file == CLUSTER_DATA_YAML_FILENAME:
                    with open(os.path.join(root, _file)) as fd:
                        _data = yaml.safe_load(fd)

                    _data["cluster_info"]["cluster-dir"] = root
                    _data["cluster_info"]["s3_bucket_name"] = _data.get("s3_bucket_name")
                    _data["cluster_info"]["s3_bucket_path"] = _data.get("s3_bucket_path")

                    clusters_data_list.append(_data)

    return clusters_data_list


def get_destroy_clusters_kwargs(clusters_data_list: List[Dict[str, Any]], user_input: UserInput) -> UserInput:
    user_input.action = DESTROY_STR
    clusters = []

    for cluster_data_from_yaml in clusters_data_list:
        cluster_data_from_yaml["cluster"].pop("expiration-time", None)
        cluster_data_from_yaml["cluster"]["cluster_info"] = cluster_data_from_yaml["cluster_info"]
        clusters.append(cluster_data_from_yaml["cluster"])

    user_input.clusters = clusters
    return user_input


def prepare_clusters_directory_from_s3_bucket(s3_bucket_name: str, s3_bucket_path: str = "", query: str = "") -> None:
    download_futures = []
    extract_futures = []
    target_files_paths = []
    _s3_client = s3_client()
    for cluster_zip_file in get_all_zip_files_from_s3_bucket(
        client=_s3_client,
        s3_bucket_name=s3_bucket_name,
        s3_bucket_path=s3_bucket_path,
        query=query,
    ):
        extracted_zip_filename: str = os.path.split(cluster_zip_file)[-1]
        extract_target_dir: str = os.path.join(
            DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY,
            extracted_zip_filename.split(".")[0],
        )
        Path(extract_target_dir).mkdir(parents=True, exist_ok=True)
        target_file_path = os.path.join(extract_target_dir, extracted_zip_filename)
        s3_bucket_cluster_zip_path = os.path.join(s3_bucket_path, cluster_zip_file)
        with ThreadPoolExecutor() as download_executor:
            LOGGER.info(f"Download S3 bucket {s3_bucket_name} to {extracted_zip_filename}")
            download_futures.append(
                download_executor.submit(
                    _s3_client.download_file,
                    **{
                        "Bucket": s3_bucket_name,
                        "Key": s3_bucket_cluster_zip_path,
                        "Filename": target_file_path,
                    },
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
                    shutil.unpack_archive,
                    **{  # type: ignore[arg-type]
                        "filename": zip_file_path,
                        "extract_dir": os.path.split(zip_file_path)[0],
                        "format": "zip",
                    },
                )
            )

        if extract_futures:
            for _ in as_completed(extract_futures):
                """
                Place holder to make sure all futures are completed.
                """


def get_all_zip_files_from_s3_bucket(
    client: "botocore.client.S3",
    s3_bucket_name: str,
    s3_bucket_path: str = "",
    query: str | None = None,
) -> Generator[str, None, None]:
    for _object in client.list_objects(Bucket=s3_bucket_name, Prefix=s3_bucket_path).get("Contents", []):
        _object_key = _object["Key"]
        if _object_key.endswith(".zip"):
            if query is None or query in _object_key:
                yield os.path.split(_object_key)[-1] if s3_bucket_path else _object_key


def destroy_clusters_from_s3_bucket_or_local_directory(user_input: UserInput) -> UserInput:
    s3_clusters_data_list = []
    data_directory_clusters_data_list = []

    s3_from_clusters_data_directory = user_input.destroy_clusters_from_install_data_directory_using_s3_bucket
    destroy_clusters_from_install_data_directory = user_input.destroy_clusters_from_install_data_directory
    destroy_clusters_from_s3_bucket_query = user_input.destroy_clusters_from_s3_bucket_query
    if user_input.destroy_clusters_from_s3_bucket or destroy_clusters_from_s3_bucket_query:
        prepare_clusters_directory_from_s3_bucket(
            s3_bucket_name=user_input.s3_bucket_name,
            s3_bucket_path=user_input.s3_bucket_path,
            query=destroy_clusters_from_s3_bucket_query,
        )

    if destroy_clusters_from_install_data_directory or s3_from_clusters_data_directory:
        clusters_from_directory = clusters_from_directories(directories=[user_input.clusters_install_data_directory])
        if destroy_clusters_from_install_data_directory:
            data_directory_clusters_data_list.extend(clusters_from_directory)

        elif s3_from_clusters_data_directory:
            for _cluster in clusters_from_directory:
                prepare_clusters_directory_from_s3_bucket(
                    s3_bucket_name=_cluster.get("s3_bucket_name", ""),
                    s3_bucket_path=_cluster.get("s3_bucket_path", ""),
                    query=os.path.split(
                        _cluster["cluster_info"].get("s3-object-name", ""),
                    )[-1],
                )

    s3_clusters_data_list.extend(clusters_from_directories(directories=[DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY]))

    updated_user_input = get_destroy_clusters_kwargs(
        clusters_data_list=s3_clusters_data_list + data_directory_clusters_data_list,
        user_input=user_input,
    )
    if not updated_user_input.clusters:
        LOGGER.error("No clusters to destroy")
        raise click.Abort()

    return updated_user_input
