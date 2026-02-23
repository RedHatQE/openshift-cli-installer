from typing import Any

from simple_logger.logger import get_logger

LOGGER = get_logger(name=__name__)


def get_managed_acm_clusters_from_user_input(
    cluster: dict[str, Any],
) -> list[str]:
    managed_acm_clusters = cluster.get("acm-clusters")

    # When user input is a single string, we need to convert it to a list
    # Single string will be when user send only one cluster: acm-clusters=cluster1
    managed_acm_clusters = managed_acm_clusters if isinstance(managed_acm_clusters, list) else [managed_acm_clusters]

    # Filter all `None` objects from the list
    return [_cluster for _cluster in managed_acm_clusters if _cluster]


def get_cluster_data_by_name_from_clusters(name: str, clusters: list[dict[str, Any]]) -> dict[str, Any]:
    for cluster in clusters:
        if cluster["name"] == name:
            return cluster

    return {}


def get_aws_credentials_for_acm_observability(
    cluster: dict[str, Any], aws_access_key_id: str, aws_secret_access_key: str
) -> tuple[str, str]:
    _aws_access_key_id = cluster.get("acm-observability-s3-access-key-id", aws_access_key_id)
    _aws_secret_access_key = cluster.get("acm-observability-s3-secret-access-key", aws_secret_access_key)
    return _aws_access_key_id, _aws_secret_access_key
