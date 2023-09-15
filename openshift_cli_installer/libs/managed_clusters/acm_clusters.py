import os
import shlex

import click
import yaml
from ocm_python_wrapper.cluster import Cluster
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.secret import Secret
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.const import (
    AWS_STR,
    ERROR_LOG_COLOR,
    SUCCESS_LOG_COLOR,
)


def install_acm(
    hub_cluster_data,
    hub_cluster_object,
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
        client=hub_cluster_object.ocp_client,
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
        client=hub_cluster_object.ocp_client,
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
    cluster_name,
    hub_cluster_object,
    acm_cluster_kubeconfig,
    managed_acm_cluster_kubeconfig,
    timeout_watch,
):
    managed_cluster_object = Cluster(
        name=cluster_name, client=hub_cluster_object.client
    )
    if not managed_cluster_object.exists:
        click.secho(f"Cluster {cluster_name} does not exist", fg=ERROR_LOG_COLOR)
        raise click.Abort()

    click.echo(f"Attach {cluster_name} to ACM hub {hub_cluster_object.name}")

    with open(managed_acm_cluster_kubeconfig, "w") as fd:
        fd.write(yaml.safe_dump(managed_cluster_object.kubeconfig))

    run_command(
        command=shlex.split(
            f"cm --kubeconfig {acm_cluster_kubeconfig} attach"
            f" {'hostedcluster' if hub_cluster_object.hypershift else 'cluster'} --cluster"
            f" {cluster_name} --cluster-kubeconfig {managed_acm_cluster_kubeconfig} "
            " --wait"
        ),
        check=False,
        verify_stderr=False,
    )

    managed_cluster = ManagedCluster(
        client=hub_cluster_object.ocp_client, name=cluster_name
    )
    managed_cluster.wait_for_condition(
        condition="ManagedClusterImportSucceeded",
        status=managed_cluster.Condition.Status.TRUE,
        timeout=timeout_watch.remaining_time(),
    )
    click.secho(
        f"{cluster_name} successfully attached to ACM Cluster"
        f" {hub_cluster_object.name}",
        fg=SUCCESS_LOG_COLOR,
    )


def install_and_attach_for_acm(
    managed_clusters, private_ssh_key_file, ssh_key_file, registry_config_file
):
    for hub_cluster_data in managed_clusters:
        timeout_watch = hub_cluster_data["timeout-watch"]
        hub_cluster_name = hub_cluster_data["name"]
        hub_cluster_object = Cluster(
            name=hub_cluster_name, client=hub_cluster_data["ocm-client"]
        )
        acm_cluster_kubeconfig = os.path.join(
            hub_cluster_data["auth-dir"], "kubeconfig"
        )
        if hub_cluster_data.get("acm"):
            install_acm(
                hub_cluster_data=hub_cluster_data,
                hub_cluster_object=hub_cluster_object,
                private_ssh_key_file=private_ssh_key_file,
                public_ssh_key_file=ssh_key_file,
                registry_config_file=registry_config_file,
                timeout_watch=timeout_watch,
            )

        for _managed_cluster_name in hub_cluster_data.get("acm-clusters", []):
            managed_acm_cluster_kubeconfig = os.path.join(
                hub_cluster_data["install-dir"],
                f"{_managed_cluster_name}-kubeconfig",
            )
            attach_cluster_to_acm(
                cluster_name=_managed_cluster_name,
                hub_cluster_object=hub_cluster_object,
                acm_cluster_kubeconfig=acm_cluster_kubeconfig,
                managed_acm_cluster_kubeconfig=managed_acm_cluster_kubeconfig,
                timeout_watch=timeout_watch,
            )
