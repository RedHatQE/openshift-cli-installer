import copy
import multiprocessing
import os
import shutil
from pathlib import Path

import click
import yaml
from clouds.aws.session_clients import s3_client

from openshift_cli_installer.libs.managed_clusters.osd_clusters import (
    osd_delete_cluster,
)
from openshift_cli_installer.libs.managed_clusters.rosa_clusters import (
    rosa_delete_cluster,
)
from openshift_cli_installer.libs.unmanaged_clusters.aws_ipi_clusters import (
    create_or_destroy_aws_ipi_cluster,
    download_openshift_install_binary,
)
from openshift_cli_installer.utils.cli_utils import assert_registry_config_file_exists
from openshift_cli_installer.utils.clusters import (
    add_ocm_client_and_env_to_cluster_dict,
)
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    CLUSTER_DATA_YAML_FILENAME,
    DESTROY_STR,
    ERROR_LOG_COLOR,
    HYPERSHIFT_STR,
    ROSA_STR,
)


def download_and_extract_s3_file(
    client, bucket, bucket_filepath, target_dir, target_filename, extracted_target_dir
):
    target_file_path = os.path.join(target_dir, target_filename)
    click.echo(f"Download {bucket_filepath} from {bucket} bucket to {target_file_path}")
    try:
        client.download_file(
            Bucket=bucket, Key=bucket_filepath, Filename=target_file_path
        )

        target_extract_dir = os.path.join(extracted_target_dir, target_filename)
        click.echo(
            f"Extract {target_filename} from {target_file_path} to {target_extract_dir}"
        )
        shutil.unpack_archive(
            filename=target_file_path,
            extract_dir=target_extract_dir,
            format="zip",
        )

    except Exception as ex:
        click.secho(
            f"{bucket_filepath} not found in {bucket} on {ex}", fg=ERROR_LOG_COLOR
        )


def destroy_clusters_from_data_dict(cluster_data_dict):
    processes = []

    for cluster_type, clusters_data_list in cluster_data_dict.items():
        for cluster_data in clusters_data_list:
            proc = multiprocessing.Process(
                target=_destroy_cluster,
                kwargs={
                    "cluster_data": cluster_data,
                    "cluster_type": cluster_type,
                },
            )

            processes.append(proc)
            proc.start()

    for proc in processes:
        proc.join()


def _destroy_cluster(cluster_data, cluster_type):
    try:
        if cluster_type == AWS_STR:
            create_or_destroy_aws_ipi_cluster(
                cluster_data=cluster_data, action=DESTROY_STR
            )
        elif cluster_type == AWS_OSD_STR:
            osd_delete_cluster(cluster_data=cluster_data)
        else:
            rosa_delete_cluster(cluster_data=cluster_data)

        s3_bucket_name = cluster_data.get("s3-bucket-name")
        if s3_bucket_name:
            delete_s3_object(cluster_data=cluster_data, s3_bucket_name=s3_bucket_name)

    except Exception as ex:
        click.secho(
            f"Cannot delete cluster {cluster_data['name']} on {ex}", fg=ERROR_LOG_COLOR
        )


def delete_s3_object(cluster_data, s3_bucket_name):
    bucket_key = cluster_data["s3-object-name"]
    click.echo(f"Delete {bucket_key} from bucket {s3_bucket_name}")
    s3_client().delete_object(Bucket=s3_bucket_name, Key=bucket_key)


def prepare_cluster_directories(s3_bucket_path, dir_prefix):
    target_dir = os.path.join("/tmp", dir_prefix)
    click.echo(f"Prepare target directory {target_dir}.")
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    if s3_bucket_path:
        Path(os.path.join(target_dir, s3_bucket_path)).mkdir(
            parents=True, exist_ok=True
        )
    extracted_target_dir = os.path.join(target_dir, "extracted_clusters_files")
    click.echo(f"Prepare target extracted directory {extracted_target_dir}.")
    Path(extracted_target_dir).mkdir(parents=True, exist_ok=True)
    return extracted_target_dir, target_dir


def set_clusters_data(cluster_dirs, clusters_dict, ocm_token):
    def _get_cluster_dict_from_yaml(_root, _cluster_filepath):
        with open(_cluster_filepath) as fd:
            _data = yaml.safe_load(fd.read())
        _data["install-dir"] = _root

        return _data

    for cluster_dir in cluster_dirs:
        for root, dirs, files in os.walk(cluster_dir):
            for _file in files:
                if _file == CLUSTER_DATA_YAML_FILENAME:
                    data = _get_cluster_dict_from_yaml(
                        _root=root, _cluster_filepath=os.path.join(root, _file)
                    )
                    clusters_dict[data["platform"]].append(data)

    _clusters_dict = copy.copy(clusters_dict)
    for cluster_type, clusters_list in clusters_dict.items():
        _clusters_dict[cluster_type] = add_ocm_client_and_env_to_cluster_dict(
            clusters=clusters_list, ocm_token=ocm_token
        )

    return _clusters_dict


def prepare_data_from_s3_bucket(s3_bucket_name, s3_bucket_path=None):
    extracted_target_dir, target_dir = prepare_cluster_directories(
        s3_bucket_path=s3_bucket_path, dir_prefix="destroy-all-clusters-from-s3-bucket"
    )

    client = s3_client()
    kwargs = {"Bucket": s3_bucket_name}
    if s3_bucket_path:
        kwargs["Prefix"] = s3_bucket_path

    get_files_from_s3_bucket(
        client=client,
        files_list=[
            cluster_file["Key"]
            for cluster_file in client.list_objects(**kwargs)["Contents"]
        ],
        extracted_target_dir=extracted_target_dir,
        s3_bucket_name=s3_bucket_name,
        target_dir=target_dir,
    )

    return extracted_target_dir, target_dir


def prepare_data_from_yaml_files(
    s3_bucket_name,
    clusters_data_dict,
    s3_bucket_path=None,
):
    extracted_target_dir, target_dir = prepare_cluster_directories(
        s3_bucket_path=s3_bucket_path, dir_prefix="destroy-clusters-from-yaml-files"
    )

    files_list = [
        cluster_data["s3-object-name"]
        for data_list in clusters_data_dict.values()
        for cluster_data in data_list
    ]

    get_files_from_s3_bucket(
        client=s3_client(),
        files_list=files_list,
        extracted_target_dir=extracted_target_dir,
        s3_bucket_name=s3_bucket_name,
        target_dir=target_dir,
    )

    # Update clusters_data_dict with path to new install-dir
    for _data_list in clusters_data_dict.values():
        for _cluster_data in _data_list:
            _cluster_data["install-dir"] = os.path.join(
                extracted_target_dir, _cluster_data["s3-object-name"]
            )

    return target_dir, clusters_data_dict


def get_files_from_s3_bucket(
    client,
    extracted_target_dir,
    s3_bucket_name,
    target_dir,
    files_list,
):
    processes = []

    for _file in files_list:
        proc = multiprocessing.Process(
            target=download_and_extract_s3_file,
            kwargs={
                "client": client,
                "bucket": s3_bucket_name,
                "bucket_filepath": _file,
                "target_dir": target_dir,
                "target_filename": _file,
                "extracted_target_dir": extracted_target_dir,
            },
        )
        processes.append(proc)
        proc.start()

    for proc in processes:
        proc.join()


def destroy_clusters(
    ocm_token,
    s3_bucket_name=None,
    s3_bucket_path=None,
    clusters_install_data_directory=None,
    registry_config_file=None,
    clusters_yaml_files=None,
    destroy_all_clusters=False,
):
    clusters_data_dict = {
        AWS_STR: [],
        ROSA_STR: [],
        HYPERSHIFT_STR: [],
        AWS_OSD_STR: [],
    }
    cluster_dirs = []
    s3_target_dirs = []
    if destroy_all_clusters:
        if clusters_install_data_directory:
            cluster_dirs.append(clusters_install_data_directory)

        if s3_bucket_name:
            s3_data_directory, s3_target_dir = prepare_data_from_s3_bucket(
                s3_bucket_name=s3_bucket_name, s3_bucket_path=s3_bucket_path
            )
            cluster_dirs.append(s3_data_directory)
            s3_target_dirs.append(s3_target_dir)

        clusters_data_dict = set_clusters_data(
            cluster_dirs=cluster_dirs,
            clusters_dict=clusters_data_dict,
            ocm_token=ocm_token,
        )

    if clusters_yaml_files:
        dir_paths = [os.path.dirname(_file) for _file in clusters_yaml_files.split(",")]
        clusters_data_dict = set_clusters_data(
            cluster_dirs=dir_paths,
            clusters_dict=clusters_data_dict,
            ocm_token=ocm_token,
        )
        target_dir, clusters_data_dict = prepare_data_from_yaml_files(
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
            clusters_data_dict=clusters_data_dict,
        )
        s3_target_dirs.append(target_dir)

    aws_clusters = clusters_data_dict["aws"]
    if aws_clusters:
        assert_registry_config_file_exists(registry_config_file=registry_config_file)
        download_openshift_install_binary(
            clusters=aws_clusters, registry_config_file=registry_config_file
        )

    destroy_clusters_from_data_dict(
        cluster_data_dict=clusters_data_dict,
    )

    for _dir in s3_target_dirs:
        shutil.rmtree(path=_dir, ignore_errors=True)
