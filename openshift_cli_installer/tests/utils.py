import json

from openshift_cli_installer.utils.cluster_versions import get_ipi_cluster_versions


def get_all_versions(_test=None):
    if _test:
        with open("openshift_cli_installer/tests/all_aws_versions.json") as fd:
            base_available_versions = json.load(fd)
    else:
        base_available_versions = get_ipi_cluster_versions()

    return base_available_versions
