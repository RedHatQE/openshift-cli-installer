import os
import shlex
from pathlib import Path

import click
import yaml
from ocm_python_wrapper.cluster import Cluster
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.secret import Secret
from ocp_resources.utils import TimeoutWatch
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.const import (
    AWS_STR,
    ERROR_LOG_COLOR,
    SUCCESS_LOG_COLOR,
)
from openshift_cli_installer.utils.general import tts


def install_acm(
    hub_cluster_data,
    ocp_client,
    private_ssh_key_file,
    public_ssh_key_file,
    registry_config_file,
    timeout_watch,
):
    cluster_name = hub_cluster_data["name"]
    click.echo(f"Installing ACM on cluster {cluster_name}")
    acm_cluster_kubeconfig = os.path.join(hub_cluster_data["auth-dir"], "kubeconfig")
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
        "aws_access_key_id": hub_cluster_data["aws-access-key-id"],
        "aws_secret_access_key": hub_cluster_data["aws-secret-access-key"],
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
    click.secho(
        f"ACM installed successfully on Cluster {cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )


def attach_cluster_to_acm(
    hub_cluster_name,
    managed_acm_cluster_name,
    acm_hub_ocp_client,
    acm_cluster_kubeconfig,
    managed_acm_cluster_kubeconfig,
    timeout_watch,
    is_hypershift=False,
):
    click.echo(f"Attach {managed_acm_cluster_name} to ACM hub {hub_cluster_name}")
    run_command(
        command=shlex.split(
            f"cm --kubeconfig {acm_cluster_kubeconfig} attach"
            f" {'hostedcluster' if is_hypershift else 'cluster'} --cluster"
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
    click.secho(
        f"{managed_acm_cluster_name} successfully attached to ACM Cluster"
        f" {hub_cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )


def install_and_attach_for_acm(
    managed_clusters, private_ssh_key_file, ssh_key_file, registry_config_file
):
    for hub_cluster_data in managed_clusters:
        timeout_watch = hub_cluster_data.get(
            "timeout-watch", TimeoutWatch(timeout=tts(ts="15m"))
        )
        ocp_client = hub_cluster_data["ocp-client"]
        ocm_client = hub_cluster_data["ocm-client"]
        acm_cluster_kubeconfig = os.path.join(
            hub_cluster_data["auth-dir"], "kubeconfig"
        )

        if hub_cluster_data.get("acm"):
            install_acm(
                hub_cluster_data=hub_cluster_data,
                ocp_client=ocp_client,
                private_ssh_key_file=private_ssh_key_file,
                public_ssh_key_file=ssh_key_file,
                registry_config_file=registry_config_file,
                timeout_watch=timeout_watch,
            )

        for _managed_cluster_name in hub_cluster_data.get("acm-clusters", []):
            managed_acm_cluster_object = Cluster(
                client=ocm_client, name=_managed_cluster_name
            )
            (
                managed_acm_cluster_kubeconfig,
                is_hypershift,
            ) = get_managed_acm_cluster_kubeconfig(
                hub_cluster_data=hub_cluster_data,
                managed_acm_cluster_name=_managed_cluster_name,
                managed_acm_cluster_object=managed_acm_cluster_object,
            )

            attach_cluster_to_acm(
                managed_acm_cluster_name=_managed_cluster_name,
                hub_cluster_name=hub_cluster_data["name"],
                acm_hub_ocp_client=ocp_client,
                acm_cluster_kubeconfig=acm_cluster_kubeconfig,
                managed_acm_cluster_kubeconfig=managed_acm_cluster_kubeconfig,
                timeout_watch=timeout_watch,
                is_hypershift=is_hypershift,
            )


def get_managed_acm_cluster_kubeconfig(
    hub_cluster_data, managed_acm_cluster_name, managed_acm_cluster_object
):
    # In case we deployed the cluster we have the kubeconfig
    managed_acm_cluster_kubeconfig = None
    is_hypershift = False
    for root, dirs, files in os.walk(hub_cluster_data["install-dir"]):
        for _dir in dirs:
            if _dir == managed_acm_cluster_name:
                managed_acm_cluster_kubeconfig = Path(
                    os.path.join(root, _dir, "auth-dir", "kubeconfig")
                )

    if not managed_acm_cluster_kubeconfig and managed_acm_cluster_object.exists:
        is_hypershift = managed_acm_cluster_object.hypershift()
        managed_acm_cluster_kubeconfig = os.path.join(
            hub_cluster_data["install-dir"],
            f"{managed_acm_cluster_name}-kubeconfig",
        )
        with open(managed_acm_cluster_kubeconfig, "w") as fd:
            fd.write(yaml.safe_dump(managed_acm_cluster_object.kubeconfig))

    if not managed_acm_cluster_kubeconfig:
        click.secho(
            f"No kubeconfig found for {managed_acm_cluster_name}", fg=ERROR_LOG_COLOR
        )
        raise click.Abort()

    return managed_acm_cluster_kubeconfig, is_hypershift
