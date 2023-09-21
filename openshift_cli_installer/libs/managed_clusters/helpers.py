import json
from datetime import datetime, timedelta

from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    ROSA_STR,
)
from openshift_cli_installer.utils.general import tts


def prepare_managed_clusters_data(
    clusters,
    aws_account_id,
    aws_secret_access_key,
    aws_access_key_id,
    gcp_service_account_file,
    create,
):
    gcp_service_account_dict = None
    if gcp_service_account_file and create:
        gcp_service_account_dict = get_service_account_dict_from_file(
            gcp_service_account_file=gcp_service_account_file
        )

    for _cluster in clusters:
        cluster_platform = _cluster["platform"]
        _cluster["cluster-name"] = _cluster["name"]
        _cluster["channel-group"] = _cluster.get("channel-group", "stable")

        _cluster["multi-az"] = _cluster.get("multi-az", False)

        if cluster_platform in (ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR):
            _cluster["aws-access-key-id"] = aws_access_key_id
            _cluster["aws-secret-access-key"] = aws_secret_access_key
            _cluster["aws-account-id"] = aws_account_id

            if cluster_platform == HYPERSHIFT_STR:
                _cluster["hosted-cp"] = "true"
                _cluster["tags"] = "dns:external"
                _cluster["machine-cidr"] = _cluster.get("cidr", "10.0.0.0/16")

        elif cluster_platform == GCP_OSD_STR and create:
            _cluster["gcp_service_account"] = gcp_service_account_dict

        expiration_time = _cluster.get("expiration-time")
        if expiration_time:
            _expiration_time = tts(ts=expiration_time)
            _cluster["expiration-time"] = (
                f"{(datetime.now() + timedelta(seconds=_expiration_time)).isoformat()}Z"
            )

    return clusters


def get_service_account_dict_from_file(gcp_service_account_file):
    with open(gcp_service_account_file) as fd:
        return json.loads(fd.read())
