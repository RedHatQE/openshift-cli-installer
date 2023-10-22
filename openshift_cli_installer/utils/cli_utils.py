import contextlib
import os

from simple_logger.logger import get_logger

LOGGER = get_logger(name=__name__)


def get_managed_acm_clusters_from_user_input(cluster):
    managed_acm_clusters = cluster.get("acm-clusters")

    # When user input is a single string, we need to convert it to a list
    # Single string will be when user send only one cluster: acm-clusters=cluster1
    managed_acm_clusters = (
        managed_acm_clusters
        if isinstance(managed_acm_clusters, list)
        else [managed_acm_clusters]
    )

    # Filter all `None` objects from the list
    return [_cluster for _cluster in managed_acm_clusters if _cluster]


def get_cluster_data_by_name_from_clusters(name, clusters):
    for cluster in clusters:
        if cluster["name"] == name:
            return cluster


@contextlib.contextmanager
def change_home_environment_on_openshift_ci():
    home_str = "HOME"
    current_home = os.environ.get(home_str)
    run_in_openshift_ci = os.environ.get("OPENSHIFT_CI") == "true"
    # If running on openshift-ci we need to change $HOME to /tmp
    if run_in_openshift_ci:
        LOGGER.info("Running in openshift ci")
        tmp_home_dir = "/tmp/"
        LOGGER.info(f"Changing {home_str} environment variable to {tmp_home_dir}")
        os.environ[home_str] = tmp_home_dir
        yield
    else:
        yield

    if run_in_openshift_ci:
        LOGGER.info(
            f"Changing {home_str} environment variable to previous value."
            f" {current_home}"
        )
        os.environ[home_str] = current_home


def get_aws_credentials_for_acm_observability(
    cluster, aws_access_key_id, aws_secret_access_key
):
    _aws_access_key_id = cluster.get(
        "acm-observability-s3-access-key-id", aws_access_key_id
    )
    _aws_secret_access_key = cluster.get(
        "acm-observability-s3-secret-access-key", aws_secret_access_key
    )
    return _aws_access_key_id, _aws_secret_access_key
