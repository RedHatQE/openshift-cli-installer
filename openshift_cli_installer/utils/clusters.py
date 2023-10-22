import contextlib
import os
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from clouds.aws.session_clients import s3_client
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.const import CLUSTER_DATA_YAML_FILENAME, DESTROY_STR


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


def get_destroy_clusters_kwargs(clusters_data_list):
    clusters_kwargs = {"action": DESTROY_STR}
    clusters_list = []
    for cluster in clusters_data_list:
        _cluster = cluster.pop("cluster")
        clusters_list.append(cluster)
        clusters_kwargs.update(cluster)
        clusters_kwargs.setdefault("clusters", []).append(_cluster)

    return clusters_kwargs


def prepare_clusters_directory_from_s3_bucket(**kwargs):
    s3_bucket_name = kwargs["s3_bucket_name"]
    s3_bucket_path = kwargs["s3_bucket_path"]
    base_extract_target_dir = os.path.join(
        "/", "tmp", "openshift-cli-installer", "s3-extracted"
    )
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
            base_extract_target_dir,
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

    return base_extract_target_dir


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
