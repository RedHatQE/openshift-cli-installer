import os
import shlex

import click
import yaml
from ocm_python_wrapper.cluster import Cluster
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.secret import Secret
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.const import AWS_STR, SUCCESS_LOG_COLOR


def install_acm(
    cluster_data,
    cluster_object,
    private_ssh_key_file,
    public_ssh_key_file,
    registry_config_file,
):
    cluster_name = cluster_data["name"]
    click.echo(f"Installing ACM on cluster {cluster_name}")
    acm_cluster_kubeconfig = os.path.join(cluster_data["auth-dir"], "kubeconfig")
    run_command(
        command=shlex.split(f"cm install acm --kubeconfig {acm_cluster_kubeconfig}")
    )
    cluster_hub = MultiClusterHub(
        client=cluster_object.ocp_client,
        name="multiclusterhub",
        namespace="open-cluster-management",
    )
    cluster_hub.wait_for_status(status=cluster_hub.Status.RUNNING)
    labels = {
        "cluster.open-cluster-management.io/credentials": "",
        "cluster.open-cluster-management.io/type": AWS_STR,
    }

    with open(private_ssh_key_file, "r") as fd:
        ssh_privatekey = fd.read()

    with open(public_ssh_key_file, "r") as fd:
        ssh_publickey = fd.read()

    secret_data = {
        "aws_access_key_id": cluster_data["aws-access-key-id"],
        "aws_secret_access_key": cluster_data["aws-secret-access-key"],
        "pullSecret": registry_config_file,
        "ssh-privatekey": ssh_privatekey,
        "ssh-publickey": ssh_publickey,
    }
    secret = Secret(
        client=cluster_object.ocp_client,
        name="aws-creds",
        namespace="default",
        label=labels,
        string_data=secret_data,
    )
    secret.deploy()
    click.secho(
        f"ACM installed successfully on Cluster {cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )

    for _cluster in cluster_data.get("acm-clusters", []):
        attach_cluster_to_acm(
            cluster_data=cluster_data,
            cluster_name=cluster_name,
            cluster_object=cluster_object,
            acm_cluster_kubeconfig=acm_cluster_kubeconfig,
        )


def attach_cluster_to_acm(
    cluster_data, cluster_name, cluster_object, acm_cluster_kubeconfig
):
    click.echo(f"Attach {cluster_name} to ACM hub, Wait for the cluster to be ready")
    managed_cluster_object = Cluster(name=cluster_name, client=cluster_object.client)
    managed_cluster_object.wait_for_cluster_ready(wait_timeout=cluster_data["timeout"])
    click.echo(f"Attach {cluster_name} to ACM hub")
    managed_acm_cluster_kubeconfig = os.path.join(
        cluster_data["install-dir"], f"{cluster_name}-kubeconfig"
    )
    with open(managed_acm_cluster_kubeconfig, "w") as fd:
        fd.write(yaml.safe_dump(managed_cluster_object.kubeconfig))

    run_command(
        command=shlex.split(
            f"cm --kubeconfig {acm_cluster_kubeconfig} attach cluster --cluster"
            f" {cluster_name} --cluster-kubeconfig"
            f" {managed_acm_cluster_kubeconfig} --wait"
        ),
        check=False,
        verify_stderr=False,
    )

    managed_cluster = ManagedCluster(
        client=cluster_object.ocp_client, name=cluster_name
    )
    managed_cluster.wait_for_condition(
        condition="ManagedClusterImportSucceeded",
        status=managed_cluster.Condition.Status.TRUE,
    )
    click.secho(
        f"{cluster_name} successfully attached to ACM Cluster {cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )
