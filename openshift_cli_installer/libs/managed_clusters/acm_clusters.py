import os
import shlex

import click
import yaml
from ocm_python_wrapper.cluster import Cluster
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.multi_cluster_observability import MultiClusterObservability
from ocp_resources.namespace import Namespace
from ocp_resources.secret import Secret
from ocp_resources.utils import TimeoutWatch
from ocp_utilities.infra import dict_base64_encode
from ocp_utilities.utils import run_command
from python_terraform import IsNotFlagged, Terraform

from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    ERROR_LOG_COLOR,
    HYPERSHIFT_STR,
    ROSA_STR,
    SUCCESS_LOG_COLOR,
)
from openshift_cli_installer.utils.general import get_manifests_path, tts


def install_acm(
    hub_cluster_data,
    ocp_client,
    private_ssh_key_file,
    public_ssh_key_file,
    registry_config_file,
    timeout_watch,
):
    cluster_name = hub_cluster_data["name"]
    aws_access_key_id = hub_cluster_data["aws-access-key-id"]
    aws_secret_access_key = hub_cluster_data["aws-secret-access-key"]
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
    click.secho(
        f"ACM installed successfully on Cluster {cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )
    if hub_cluster_data.get("acm_observability"):
        enable_observability(
            ocp_client=ocp_client,
            s3_bucket_endpoint=f"s3.{hub_cluster_data['region']}.amazonaws.com",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            cluster_name=cluster_name,
        )


def attach_cluster_to_acm(
    hub_cluster_name,
    managed_acm_cluster_name,
    acm_hub_ocp_client,
    acm_cluster_kubeconfig,
    managed_acm_cluster_kubeconfig,
    timeout_watch,
):
    click.echo(f"Attach {managed_acm_cluster_name} to ACM hub {hub_cluster_name}")
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
    click.secho(
        f"{managed_acm_cluster_name} successfully attached to ACM Cluster"
        f" {hub_cluster_name}",
        fg=SUCCESS_LOG_COLOR,
    )


def install_and_attach_for_acm(
    managed_clusters,
    private_ssh_key_file,
    ssh_key_file,
    registry_config_file,
    clusters_install_data_directory,
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

        for _managed_acm_clusters in hub_cluster_data.get("acm-clusters", []):
            _managed_cluster_name = _managed_acm_clusters["name"]
            _managed_cluster_platform = _managed_acm_clusters["platform"]
            managed_acm_cluster_kubeconfig = get_managed_acm_cluster_kubeconfig(
                hub_cluster_data=hub_cluster_data,
                managed_acm_cluster_name=_managed_cluster_name,
                managed_cluster_platform=_managed_cluster_platform,
                ocm_client=ocm_client,
                clusters_install_data_directory=clusters_install_data_directory,
            )

            attach_cluster_to_acm(
                managed_acm_cluster_name=_managed_cluster_name,
                hub_cluster_name=hub_cluster_data["name"],
                acm_hub_ocp_client=ocp_client,
                acm_cluster_kubeconfig=acm_cluster_kubeconfig,
                managed_acm_cluster_kubeconfig=managed_acm_cluster_kubeconfig,
                timeout_watch=timeout_watch,
            )


def get_managed_acm_cluster_kubeconfig(
    hub_cluster_data,
    managed_acm_cluster_name,
    managed_cluster_platform,
    ocm_client,
    clusters_install_data_directory,
):
    # In case we deployed the cluster we have the kubeconfig
    managed_acm_cluster_kubeconfig = None
    if managed_cluster_platform in (ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR):
        managed_acm_cluster_object = Cluster(
            client=ocm_client, name=managed_acm_cluster_name
        )
        managed_acm_cluster_kubeconfig = os.path.join(
            hub_cluster_data["install-dir"],
            f"{managed_acm_cluster_name}-kubeconfig",
        )
        with open(managed_acm_cluster_kubeconfig, "w") as fd:
            fd.write(yaml.safe_dump(managed_acm_cluster_object.kubeconfig))

    elif managed_cluster_platform == AWS_STR:
        managed_acm_cluster_kubeconfig = get_cluster_kubeconfig_from_install_dir(
            clusters_install_data_directory=clusters_install_data_directory,
            cluster_name=managed_acm_cluster_name,
            cluster_platform=managed_cluster_platform,
        )

    if not managed_acm_cluster_kubeconfig:
        click.secho(
            f"No kubeconfig found for {managed_acm_cluster_name}", fg=ERROR_LOG_COLOR
        )
        raise click.Abort()

    return managed_acm_cluster_kubeconfig


def get_cluster_kubeconfig_from_install_dir(
    clusters_install_data_directory, cluster_name, cluster_platform
):
    cluster_install_dir = os.path.join(
        clusters_install_data_directory, cluster_platform, cluster_name
    )
    if not os.path.exists(cluster_install_dir):
        click.secho(
            f"Install dir {cluster_install_dir} not found for {cluster_name}",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()

    return os.path.join(cluster_install_dir, "auth", "kubeconfig")


def enable_observability(
    ocp_client,
    s3_bucket_endpoint,
    aws_access_key_id,
    aws_secret_access_key,
    cluster_name,
):
    s3_bucket_name = prepare_vpc_with_endpoint(cluster_name=cluster_name)
    string_data_json = {
        "type": "s3",
        "config": {
            "bucket": s3_bucket_name,
            "endpoint": s3_bucket_endpoint,
            "insecure": "true",
            "access_key": aws_access_key_id,
            "secret_key": aws_secret_access_key,
        },
    }
    s3_secret_data = {"thanos.yaml": dict_base64_encode(string_data_json)}

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
    s3_secret = Secret(
        client=ocp_client,
        name="thanos-object-storage",
        namespace=open_cluster_management_observability_ns.name,
        type="Opaque",
        data_dict=s3_secret_data,
    )
    s3_secret.deploy(wait=True)

    multi_cluster_observability_data = {"name": s3_secret.name, "key": "thanos.yaml"}
    multi_cluster_observability = MultiClusterObservability(
        client=ocp_client,
        name="observability",
        metric_object_storage=multi_cluster_observability_data,
    )
    multi_cluster_observability.deploy(wait=True)


def terraform_init():
    terraform = Terraform(
        working_dir=os.path.join(get_manifests_path(), "vpc_with_endpoint"),
    )
    terraform.init()
    return terraform


def destroy_vpc_with_endpoint(cluster_name, terraform):
    click.echo(
        f"Destroy VPC with endpoint for ACM observability for cluster {cluster_name}"
    )
    rc, _, err = terraform.destroy(
        force=IsNotFlagged,
        auto_approve=True,
        capture_output=True,
    )
    if rc != 0:
        click.secho(
            "Failed to destroy VPC with endpoint for ACM observability for cluster"
            f" {cluster_name} with error: {err}"
        )
        raise click.Abort()


def prepare_vpc_with_endpoint(cluster_name):
    click.echo(
        f"Preparing VPC with endpoint for ACM observability for cluster {cluster_name}"
    )
    terraform = terraform_init()
    terraform.plan(dir_or_plan="vpc_with_endpoint.plan")
    rc, _, err = terraform.apply(capture_output=True, skip_plan=True, auto_approve=True)
    if rc != 0:
        click.secho(
            f"Create hypershift VPC for cluster {cluster_name} failed with"
            f" error: {err}, rolling back.",
            fg=ERROR_LOG_COLOR,
        )
        # Clean up already created resources from the plan
        destroy_vpc_with_endpoint(cluster_name=cluster_name, terraform=terraform)
        raise click.Abort()

    return terraform.output()["openshift-observability-bucket"]["value"]
