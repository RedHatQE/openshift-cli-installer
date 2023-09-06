from datetime import datetime, timedelta

from openshift_cli_installer.utils.const import HYPERSHIFT_STR, TIMEOUT_60MIN
from openshift_cli_installer.utils.general import tts


def prepare_managed_clusters_data(
    clusters,
    aws_account_id,
    aws_secret_access_key,
    aws_access_key_id,
):
    for _cluster in clusters:
        _cluster["cluster-name"] = _cluster["name"]
        _cluster["timeout"] = tts(ts=_cluster.get("timeout", TIMEOUT_60MIN))
        _cluster["channel-group"] = _cluster.get("channel-group", "stable")
        _cluster["aws-access-key-id"] = aws_access_key_id
        _cluster["aws-secret-access-key"] = aws_secret_access_key
        _cluster["aws-account-id"] = aws_account_id
        _cluster["multi-az"] = _cluster.get("multi-az", False)
        if _cluster["platform"] == HYPERSHIFT_STR:
            _cluster["hosted-cp"] = "true"
            _cluster["tags"] = "dns:external"
            _cluster["machine-cidr"] = _cluster.get("cidr", "10.0.0.0/16")

        expiration_time = _cluster.get("expiration-time")
        if expiration_time:
            _expiration_time = tts(ts=expiration_time)
            _cluster["expiration-time"] = (
                f"{(datetime.now() + timedelta(seconds=_expiration_time)).isoformat()}Z"
            )

    return clusters
