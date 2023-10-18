import contextlib
import copy
import os
import shlex

import rosa.cli
import yaml
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocm_python_wrapper.versions import Versions
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.cluster_versions import set_clusters_versions
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    CLUSTER_DATA_YAML_FILENAME,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    ROSA_STR,
)


def get_ocm_client(ocm_token, ocm_env):
    return OCMPythonClient(
        token=ocm_token,
        endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        api_host=ocm_env,
        discard_unknown_keys=True,
    ).client


def dump_cluster_data_to_file(cluster_data):
    _cluster_data = copy.copy(cluster_data)
    _cluster_data.pop("ocm-client", "")
    _cluster_data.pop("timeout-watch", "")
    _cluster_data.pop("ocp-client", "")
    _cluster_data.pop("cluster-object", "")
    with open(
        os.path.join(_cluster_data["install-dir"], CLUSTER_DATA_YAML_FILENAME), "w"
    ) as fd:
        fd.write(yaml.dump(_cluster_data))


def update_rosa_osd_clusters_versions(clusters, _test=False, _test_versions_dict=None):
    if _test:
        base_available_versions_dict = _test_versions_dict
    else:
        base_available_versions_dict = {}
        for cluster_data in clusters:
            if cluster_data["platform"] in (AWS_OSD_STR, GCP_OSD_STR):
                base_available_versions_dict.update(
                    Versions(client=cluster_data["ocm-client"]).get(
                        channel_group=cluster_data["channel-group"]
                    )
                )

            elif cluster_data["platform"] in (ROSA_STR, HYPERSHIFT_STR):
                channel_group = cluster_data["channel-group"]
                base_available_versions = rosa.cli.execute(
                    command=(
                        f"list versions --channel-group={channel_group} "
                        f"{'--hosted-cp' if cluster_data['platform'] == HYPERSHIFT_STR else ''}"
                    ),
                    aws_region=cluster_data["region"],
                    ocm_client=cluster_data["ocm-client"],
                )["out"]
                _all_versions = [ver["raw_id"] for ver in base_available_versions]
                base_available_versions_dict.setdefault(channel_group, []).extend(
                    _all_versions
                )

    return set_clusters_versions(
        clusters=clusters,
        base_available_versions=base_available_versions_dict,
    )

    return cluster_data


def get_kubeconfig_path(cluster_data):
    return os.path.join(cluster_data["auth-dir"], "kubeconfig")


@contextlib.contextmanager
def get_kubeadmin_token(cluster_dir, api_url):
    with open(os.path.join(cluster_dir, "auth", "kubeadmin-password")) as fd:
        kubeadmin_password = fd.read()
    run_command(
        shlex.split(f"oc login {api_url} -u kubeadmin -p {kubeadmin_password}"),
        hide_log_command=True,
    )
    yield run_command(
        shlex.split("oc whoami -t"),
        hide_log_command=True,
    )[1].strip()
    run_command(shlex.split("oc logout"))
