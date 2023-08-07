import multiprocessing
import os
import re
import shutil
from pathlib import Path

import click
import yaml
from clouds.aws.session_clients import s3_client
from libs.aws_ipi_clusters import (
    create_or_destroy_aws_ipi_cluster,
    download_openshift_install_binary,
)
from libs.rosa_clusters import rosa_delete_cluster
from utils.const import AWS_STR

S3_EXTRACTED_DATA_FILES_DIR_NAME = "extracted_clusters_files"


def download_and_extract_s3_file(
    client, bucket, bucket_filepath, target_dir, target_filename, extracted_target_dir
):
    target_file_path = os.path.join(target_dir, target_filename)
    click.echo(f"Download {bucket_filepath} from {bucket} bucket to {target_file_path}")
    client.download_file(Bucket=bucket, Key=bucket_filepath, Filename=target_file_path)

    target_extract_dir = os.path.join(extracted_target_dir, target_filename)
    click.echo(
        f"Extract {target_filename} from {target_file_path} to {target_extract_dir}"
    )
    shutil.unpack_archive(
        filename=target_file_path,
        extract_dir=target_extract_dir,
        format="zip",
    )


def prepare_data_from_s3_bucket(s3_bucket_name, s3_bucket_path=None):
    extracted_target_dir, target_dir = prepare_cluster_directories(
        s3_bucket_path=s3_bucket_path, dir_prefix="destroy-all-clusters-from-s3-bucket"
    )

    get_all_files_from_s3_bucket(
        extracted_target_dir=extracted_target_dir,
        s3_bucket_name=s3_bucket_name,
        s3_bucket_path=s3_bucket_path,
        target_dir=target_dir,
    )

    return extracted_target_dir, target_dir


def _destroy_all_download_installer_binary(cluster_data_dict, registry_config_file):
    aws_clusters = cluster_data_dict["aws"]
    if aws_clusters:
        download_openshift_install_binary(
            clusters=aws_clusters, registry_config_file=registry_config_file
        )


def delete_all_clusters(cluster_data_dict, s3_bucket_name=None):
    processes = []
    for cluster_type in cluster_data_dict:
        for cluster_data in cluster_data_dict[cluster_type]:
            proc = multiprocessing.Process(
                target=_destroy_cluster,
                kwargs={
                    "cluster_data": cluster_data,
                    "cluster_type": cluster_type,
                    "s3_bucket_name": s3_bucket_name,
                },
            )

            processes.append(proc)
            proc.start()
    for proc in processes:
        proc.join()


def _destroy_cluster(cluster_data, cluster_type, s3_bucket_name=None):
    try:
        if cluster_type == AWS_STR:
            create_or_destroy_aws_ipi_cluster(
                cluster_data=cluster_data, action="destroy"
            )
        else:
            rosa_delete_cluster(cluster_data=cluster_data)

        if s3_bucket_name:
            delete_s3_object(cluster_data=cluster_data, s3_bucket_name=s3_bucket_name)
    except click.exceptions.Abort:
        click.echo(f"Cannot delete cluster {cluster_data['name']}")
        # TODO: Delete S3 file is a cluster is not found; need to add more exception logic to know when to delete.
        # if s3_bucket_name:
        #   delete_s3_object(cluster_data=cluster_data, s3_bucket_name=s3_bucket_name)


def delete_s3_object(cluster_data, s3_bucket_name):
    bucket_key = cluster_data["bucket_filename"]
    click.echo(f"Delete {bucket_key} from bucket {s3_bucket_name}")
    s3_client().delete_object(Bucket=s3_bucket_name, Key=bucket_key)


def get_all_files_from_s3_bucket(
    extracted_target_dir,
    s3_bucket_name,
    s3_bucket_path,
    target_dir,
):
    client = s3_client()
    kwargs = {"Bucket": s3_bucket_name}
    if s3_bucket_path:
        kwargs["Prefix"] = s3_bucket_path

    processes = []
    for cluster_file in client.list_objects(**kwargs):
        name = cluster_file["Key"]
        proc = multiprocessing.Process(
            target=download_and_extract_s3_file,
            kwargs={
                "client": client,
                "bucket": s3_bucket_name,
                "bucket_filepath": name,
                "target_dir": target_dir,
                "target_filename": name,
                "extracted_target_dir": extracted_target_dir,
            },
        )
        processes.append(proc)
        proc.start()
    for proc in processes:
        proc.join()


def prepare_cluster_directories(s3_bucket_path, dir_prefix):
    target_dir = os.path.join("/tmp", dir_prefix)
    click.echo(f"Prepare target directory {target_dir}.")
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    if s3_bucket_path:
        Path(os.path.join(target_dir, s3_bucket_path)).mkdir(
            parents=True, exist_ok=True
        )
    extracted_target_dir = os.path.join(target_dir, S3_EXTRACTED_DATA_FILES_DIR_NAME)
    click.echo(f"Prepare target extracted directory {extracted_target_dir}.")
    Path(extracted_target_dir).mkdir(parents=True, exist_ok=True)
    return extracted_target_dir, target_dir


def get_clusters_data(cluster_dirs, clusters_dict):
    def _get_cluster_dict_from_yaml(_cluster_filepath):
        with open(_cluster_filepath) as fd:
            _data = yaml.safe_load(fd.read())
        _data["install-dir"] = root
        if S3_EXTRACTED_DATA_FILES_DIR_NAME in root and not _data.get(
            "bucket_filename"
        ):
            _data["bucket_filename"] = re.match(
                rf".*{S3_EXTRACTED_DATA_FILES_DIR_NAME}/(.*)", root
            ).group(1)
        return _data

    for cluster_dir in cluster_dirs:
        for root, dirs, files in os.walk(cluster_dir):
            for _file in files:
                if _file == "cluster_data.yaml":
                    data = _get_cluster_dict_from_yaml(
                        _cluster_filepath=os.path.join(root, _file)
                    )
                    clusters_dict[data["platform"]].append(data)

    return clusters_dict


def prepare_data_from_yaml_files(s3_bucket_path, s3_bucket_name, clusters_data_dict):
    extracted_target_dir, target_dir = prepare_cluster_directories(
        s3_bucket_path=s3_bucket_path, dir_prefix="destroy-clusters-from-yaml-files"
    )
    client = s3_client()
    processes = []

    for cluster_file in [
        cluster_data["bucket_filename"]
        for data_list in clusters_data_dict.values()
        for cluster_data in data_list
    ]:
        proc = multiprocessing.Process(
            target=download_and_extract_s3_file,
            kwargs={
                "client": client,
                "bucket": s3_bucket_name,
                "bucket_filepath": cluster_file,
                "target_dir": target_dir,
                "target_filename": cluster_file,
                "extracted_target_dir": extracted_target_dir,
            },
        )
        processes.append(proc)
        proc.start()
    for proc in processes:
        proc.join()

    return target_dir


def _destroy_all_clusters(
    s3_bucket_name=None,
    s3_bucket_path=None,
    clusters_install_data_directory=None,
    registry_config_file=None,
    clusters_yaml_files=None,
    destroy_all_clusters=False,
):
    clusters_data_dict = {"aws": [], "rosa": [], "hypershift": []}
    s3_target_dirs = []
    if destroy_all_clusters:
        cluster_dirs = (
            [clusters_install_data_directory] if clusters_install_data_directory else []
        )

        if s3_bucket_name:
            s3_data_directory, s3_target_dir = prepare_data_from_s3_bucket(
                s3_bucket_name=s3_bucket_name, s3_bucket_path=s3_bucket_path
            )
            cluster_dirs.append(s3_data_directory)
            s3_target_dirs.append(s3_target_dir)

        clusters_data_dict = get_clusters_data(
            cluster_dirs=cluster_dirs, clusters_dict=clusters_data_dict
        )

    if clusters_yaml_files:
        dir_paths = [os.path.dirname(_file) for _file in clusters_yaml_files.split(",")]
        clusters_data_dict = get_clusters_data(
            cluster_dirs=dir_paths, clusters_dict=clusters_data_dict
        )
        target_dir = prepare_data_from_yaml_files(
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
            clusters_data_dict=clusters_data_dict,
        )
        s3_target_dirs.append(target_dir)

    _destroy_all_download_installer_binary(
        cluster_data_dict=clusters_data_dict, registry_config_file=registry_config_file
    )

    delete_all_clusters(
        cluster_data_dict=clusters_data_dict, s3_bucket_name=s3_bucket_name
    )

    for _dir in s3_target_dirs:
        shutil.rmtree(path=_dir, ignore_errors=True)
