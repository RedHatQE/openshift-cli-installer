import base64
import os
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from clouds.aws.session_clients import s3_client
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.multi_cluster_observability import MultiClusterObservability
from ocp_resources.namespace import Namespace
from ocp_resources.secret import Secret
from ocp_resources.utils import TimeoutWatch
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.cli_utils import (
    click_echo,
    get_cluster_data_by_name_from_clusters,
    get_managed_acm_clusters_from_user_input,
)
from openshift_cli_installer.utils.clusters import get_kubeconfig_path
from openshift_cli_installer.utils.const import (
    AWS_BASED_PLATFORMS,
    AWS_STR,
    GCP_OSD_STR,
)
from openshift_cli_installer.utils.general import tts


def install_acm(
    hub_cluster_data,
    ocp_client,
    private_ssh_key_file,
    public_ssh_key_file,
    registry_config_file,
    timeout_watch,
    acm_cluster_kubeconfig,
):
    section = "Install ACM"
    cluster_name = hub_cluster_data["name"]
    platform = hub_cluster_data["platform"]
    aws_access_key_id = hub_cluster_data["aws-access-key-id"]
    aws_secret_access_key = hub_cluster_data["aws-secret-access-key"]
    click_echo(
        name=cluster_name, platform=platform, section=section, msg="Installing ACM"
    )
    run_command(
        command=shlex.split(f"cm install acm --kubeconfig {acm_cluster_kubeconfig}"),
    )
    cluster_hub = MultiClusterHub(
        client=ocp_client,
        name="multiclusterhub",
        namespace="open-cluster-management",
    )
    cluster_hub.wait_for_status(
        status=cluster_hub.Status.RUNNING, timeout=timeout_watch.remaining_time()
    )
    labels = {
        f"{cluster_hub.api_group}/credentials": "",
        f"{cluster_hub.api_group}/type": AWS_STR,
    }

    with open(private_ssh_key_file, "r") as fd:
        ssh_privatekey = fd.read()

    with open(public_ssh_key_file, "r") as fd:
        ssh_publickey = fd.read()

    secret_data = {
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "pullSecret": registry_config_file,
        "ssh-privatekey": ssh_privatekey,
        "ssh-publickey": ssh_publickey,
    }
    secret = Secret(
        client=ocp_client,
        name="aws-creds",
        namespace="default",
        label=labels,
        string_data=secret_data,
    )
    secret.deploy(wait=True)
    click_echo(
        name=cluster_name,
        platform=platform,
        section=section,
        msg="ACM installed successfully",
        success=True,
    )
    if hub_cluster_data.get("acm-observability"):
        enable_observability(
            hub_cluster_data=hub_cluster_data,
            timeout_watch=timeout_watch,
        )


def attach_cluster_to_acm(
    hub_cluster_name,
    managed_acm_cluster_name,
    acm_hub_ocp_client,
    acm_cluster_kubeconfig,
    managed_acm_cluster_kubeconfig,
    timeout_watch,
    managed_cluster_platform,
):
    section = "Attach cluster to ACM hub"
    click_echo(
        name=hub_cluster_name,
        platform=managed_cluster_platform,
        section=section,
        msg=f"Attach to ACM hub {hub_cluster_name}",
    )
    run_command(
        command=shlex.split(
            f"cm --kubeconfig {acm_cluster_kubeconfig} attach cluster --cluster"
            f" {managed_acm_cluster_name} --cluster-kubeconfig"
            f" {managed_acm_cluster_kubeconfig}  --wait"
        ),
        check=False,
        verify_stderr=False,
    )

    managed_cluster = ManagedCluster(
        client=acm_hub_ocp_client, name=managed_acm_cluster_name
    )
    managed_cluster.wait_for_condition(
        condition="ManagedClusterImportSucceeded",
        status=managed_cluster.Condition.Status.TRUE,
        timeout=timeout_watch.remaining_time(),
    )
    click_echo(
        name=managed_acm_cluster_name,
        platform=managed_cluster_platform,
        section=section,
        msg=f"successfully attached to ACM Cluster {hub_cluster_name}",
        success=True,
    )


def install_and_attach_for_acm(
    managed_clusters,
    private_ssh_key_file,
    ssh_key_file,
    registry_config_file,
    clusters_install_data_directory,
    parallel,
):
    for hub_cluster_data in managed_clusters:
        if hub_cluster_data.get("acm"):
            timeout_watch = hub_cluster_data.get(
                "timeout-watch", TimeoutWatch(timeout=tts(ts="15m"))
            )
            acm_cluster_ocp_client = hub_cluster_data["ocp-client"]
            acm_cluster_kubeconfig = get_kubeconfig_path(cluster_data=hub_cluster_data)

            install_acm(
                hub_cluster_data=hub_cluster_data,
                ocp_client=acm_cluster_ocp_client,
                private_ssh_key_file=private_ssh_key_file,
                public_ssh_key_file=ssh_key_file,
                registry_config_file=registry_config_file,
                timeout_watch=timeout_watch,
                acm_cluster_kubeconfig=acm_cluster_kubeconfig,
            )

            attach_clusters_to_acm_hub(
                clusters_install_data_directory=clusters_install_data_directory,
                ocp_client=acm_cluster_ocp_client,
                acm_cluster_kubeconfig=acm_cluster_kubeconfig,
                timeout_watch=timeout_watch,
                hub_cluster_data=hub_cluster_data,
                managed_clusters=managed_clusters,
                parallel=parallel,
            )

    return managed_clusters


def get_cluster_kubeconfig_from_install_dir(
    clusters_install_data_directory, cluster_name, cluster_platform
):
    cluster_install_dir = os.path.join(
        clusters_install_data_directory, cluster_platform, cluster_name
    )
    if not os.path.exists(cluster_install_dir):
        click_echo(
            name=cluster_name,
            platform=cluster_platform,
            section="Get cluster kubeconfig from install dir",
            msg=f"Install dir {cluster_install_dir} not found for",
            error=True,
        )
        raise click.Abort()

    return os.path.join(cluster_install_dir, "auth", "kubeconfig")


def enable_observability(
    hub_cluster_data,
    timeout_watch,
):
    section = "Observability"
    thanos_secret_data = None
    _s3_client = None
    ocp_client = hub_cluster_data["ocp-client"]
    cluster_name = hub_cluster_data["name"]
    bucket_name = f"{cluster_name}-observability-{hub_cluster_data['shortuuid']}"
    hub_cluster_platform = hub_cluster_data["platform"]

    if hub_cluster_platform in AWS_BASED_PLATFORMS:
        _s3_client = s3_client()
        aws_access_key_id = hub_cluster_data["aws-access-key-id"]
        aws_secret_access_key = hub_cluster_data["aws-secret-access-key"]
        aws_region = hub_cluster_data["region"]
        s3_secret_data = f"""
        type: s3
        config:
          bucket: {bucket_name}
          endpoint: s3.{aws_region}.amazonaws.com
          insecure: true
          access_key: {aws_access_key_id}
          secret_key: {aws_secret_access_key}
        """
        s3_secret_data_bytes = s3_secret_data.encode("ascii")
        thanos_secret_data = {
            "thanos.yaml": base64.b64encode(s3_secret_data_bytes).decode("utf-8")
        }
        _s3_client.create_bucket(Bucket=bucket_name.lower())

    elif hub_cluster_platform == GCP_OSD_STR:
        # TODO: Add GCP support
        pass

    else:
        click_echo(
            name=cluster_name,
            platform=hub_cluster_platform,
            section=section,
            msg="Platform not supported",
            error=True,
        )
        raise click.Abort()

    try:
        open_cluster_management_observability_ns = Namespace(
            client=ocp_client, name="open-cluster-management-observability"
        )
        open_cluster_management_observability_ns.deploy(wait=True)
        openshift_pull_secret = Secret(
            client=ocp_client, name="pull-secret", namespace="openshift-config"
        )
        observability_pull_secret = Secret(
            client=ocp_client,
            name="multiclusterhub-operator-pull-secret",
            namespace=open_cluster_management_observability_ns.name,
            data_dict={
                ".dockerconfigjson": openshift_pull_secret.instance.data[
                    ".dockerconfigjson"
                ]
            },
            type="kubernetes.io/dockerconfigjson",
        )
        observability_pull_secret.deploy(wait=True)
        thanos_secret = Secret(
            client=ocp_client,
            name="thanos-object-storage",
            namespace=open_cluster_management_observability_ns.name,
            type="Opaque",
            data_dict=thanos_secret_data,
        )
        thanos_secret.deploy(wait=True)

        multi_cluster_observability_data = {
            "name": thanos_secret.name,
            "key": "thanos.yaml",
        }
        multi_cluster_observability = MultiClusterObservability(
            client=ocp_client,
            name="observability",
            metric_object_storage=multi_cluster_observability_data,
        )
        multi_cluster_observability.deploy(wait=True)
        multi_cluster_observability.wait_for_condition(
            condition=multi_cluster_observability.Condition.READY,
            status=multi_cluster_observability.Condition.Status.TRUE,
            timeout=timeout_watch.remaining_time(),
        )
        click_echo(
            name=cluster_name,
            platform=hub_cluster_platform,
            section=section,
            msg="Successfully enabled observability",
            success=True,
        )
    except Exception as ex:
        click_echo(
            name=cluster_name,
            platform=hub_cluster_platform,
            section=section,
            msg=f"Failed to enable observability. error: {ex}",
            error=True,
        )
        if hub_cluster_platform in AWS_BASED_PLATFORMS:
            for _bucket in _s3_client.list_buckets()["Buckets"]:
                if _bucket["Name"] == bucket_name:
                    _s3_client.delete_bucket(Bucket=bucket_name)

        raise click.Abort()


def attach_clusters_to_acm_hub(
    clusters_install_data_directory,
    ocp_client,
    acm_cluster_kubeconfig,
    timeout_watch,
    hub_cluster_data,
    managed_clusters,
    parallel,
):
    managed_acm_clusters = get_managed_acm_clusters_from_user_input(
        cluster=hub_cluster_data
    )
    if not managed_acm_clusters:
        return

    section = "Attach cluster to ACM hub"
    futures = []
    processed_clusters = []
    hub_cluster_data_name = hub_cluster_data["name"]
    hub_cluster_data_platform = hub_cluster_data["platform"]
    with ThreadPoolExecutor() as executor:
        for _managed_acm_cluster in managed_acm_clusters:
            _managed_acm_cluster_data = get_cluster_data_by_name_from_clusters(
                name=_managed_acm_cluster, clusters=managed_clusters
            )
            _managed_cluster_name = _managed_acm_cluster_data["name"]
            _managed_cluster_platform = _managed_acm_cluster_data["platform"]
            managed_acm_cluster_kubeconfig = get_cluster_kubeconfig_from_install_dir(
                cluster_name=_managed_cluster_name,
                cluster_platform=_managed_cluster_platform,
                clusters_install_data_directory=clusters_install_data_directory,
            )
            action_kwargs = {
                "managed_acm_cluster_name": _managed_cluster_name,
                "hub_cluster_name": hub_cluster_data_name,
                "acm_hub_ocp_client": ocp_client,
                "acm_cluster_kubeconfig": acm_cluster_kubeconfig,
                "managed_acm_cluster_kubeconfig": managed_acm_cluster_kubeconfig,
                "timeout_watch": timeout_watch,
                "managed_cluster_platform": _managed_cluster_platform,
            }
            click_echo(
                name=hub_cluster_data_name,
                platform=hub_cluster_data_platform,
                section=section,
                msg=f"Attach {_managed_cluster_name} to ACM hub",
            )

            if parallel:
                futures.append(executor.submit(attach_cluster_to_acm, **action_kwargs))
            else:
                processed_clusters.append(attach_cluster_to_acm(**action_kwargs))

    if futures:
        for result in as_completed(futures):
            if result.exception():
                click_echo(
                    name=hub_cluster_data_name,
                    platform=hub_cluster_data_platform,
                    section=section,
                    msg=f"Failed to attach {_managed_cluster_name} to ACM hub",
                    error=True,
                )
                raise click.Abort()
