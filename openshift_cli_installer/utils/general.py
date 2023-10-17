import os
import re
import shutil
from functools import wraps
from importlib.util import find_spec
from time import sleep

import click
from clouds.aws.session_clients import s3_client
from simple_logger.logger import get_logger

LOGGER = get_logger(name=__name__)


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


def bucket_object_name(cluster_data, s3_bucket_path=None):
    return (
        f"{f'{s3_bucket_path}/' if s3_bucket_path else ''}{cluster_data['name']}-{cluster_data['shortuuid']}.zip"
    )


def get_manifests_path():
    manifests_path = os.path.join("openshift_cli_installer", "manifests")
    if not os.path.isdir(manifests_path):
        manifests_path = os.path.join(
            find_spec("openshift_cli_installer").submodule_search_locations[0],
            "manifests",
        )
    return manifests_path


# TODO: Move to own repository.
def tts(ts):
    """
    Convert time string to seconds.

    Args:
        ts (str): time string to convert, can be and int followed by s/m/h
            if only numbers was sent return int(ts)

    Example:
        >>> tts(ts="1h")
        3600
        >>> tts(ts="3600")
        3600

    Returns:
        int: Time in seconds
    """
    try:
        time_and_unit = re.match(r"(?P<time>\d+)(?P<unit>\w)", str(ts)).groupdict()
    except AttributeError:
        return int(ts)

    _time = int(time_and_unit["time"])
    _unit = time_and_unit["unit"].lower()
    if _unit == "s":
        return _time
    elif _unit == "m":
        return _time * 60
    elif _unit == "h":
        return _time * 60 * 60
    else:
        return int(ts)


def delete_cluster_s3_buckets(cluster_data):
    cluster_name = cluster_data["name"]
    LOGGER.info(f"Deleting S3 bucket for cluster {cluster_name}")
    buckets_to_delete = []
    _s3_client = s3_client()
    for _bucket in _s3_client.list_buckets()["Buckets"]:
        if _bucket["Name"].startswith(cluster_name):
            buckets_to_delete.append(_bucket["Name"])

    for _bucket in buckets_to_delete:
        LOGGER.info(f"{cluster_name}: Deleting S3 bucket {_bucket}")
        for _object in _s3_client.list_objects(Bucket=_bucket)["Contents"]:
            _s3_client.delete_object(Bucket=_bucket, Key=_object["Key"])

        _s3_client.delete_bucket(Bucket=_bucket)
