import multiprocessing
import os
from pathlib import Path

import click
import rosa.cli
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from libs.aws_ipi_clusters import (
    create_install_config_file,
    create_or_destroy_aws_ipi_cluster,
    download_openshift_install_binary,
)
from libs.rosa_clusters import (
    prepare_managed_clusters_data,
    rosa_create_cluster,
    rosa_delete_cluster,
)
from utils.click_dict_type import DictParamType
from utils.const import AWS_STR, HYPERSHIFT_STR, ROSA_STR
from utils.helpers import get_ocm_client


def get_clusters_by_type(clusters):
    aws_ipi_clusters = [
        _cluster for _cluster in clusters if _cluster["platform"] == AWS_STR
    ]
    rosa_clusters = [
        _cluster for _cluster in clusters if _cluster["platform"] == ROSA_STR
    ]
    hypershift_clusters = [
        _cluster for _cluster in clusters if _cluster["platform"] == HYPERSHIFT_STR
    ]
    return aws_ipi_clusters, rosa_clusters, hypershift_clusters


def is_platform_supported(clusters):
    supported_platform = (AWS_STR, ROSA_STR, HYPERSHIFT_STR)
    for _cluster in clusters:
        _platform = _cluster["platform"]
        if _platform not in supported_platform:
            click.echo(f"Cluster platform '{_platform}' is not supported.\n{_cluster}")
            raise click.Abort()


def rosa_regions(ocm_client):
    return rosa.cli.execute(
        command="list regions",
        aws_region="us-west-2",
        ocm_client=ocm_client,
    )["out"]


def hypershift_regions(ocm_client):
    return [
        region["id"]
        for region in rosa_regions(ocm_client=ocm_client)
        if region["supports_hypershift"] is True
    ]


def is_region_support_hypershift(ocm_client, hypershift_clusters):
    _hypershift_regions = hypershift_regions(ocm_client=ocm_client)
    for _cluster in hypershift_clusters:
        _region = _cluster["region"]
        if _region not in _hypershift_regions:
            click.echo(
                f"region '{_region}' does not supported {HYPERSHIFT_STR}."
                f"\nSupported hypershift regions are: {_hypershift_regions}"
                f"\n{_cluster}"
            )
            raise click.Abort()


def generate_cluster_dirs_path(clusters, base_directory):
    for _cluster in clusters:
        cluster_dir = os.path.join(
            base_directory, _cluster["platform"], _cluster["name"]
        )
        _cluster["install-dir"] = cluster_dir
        auth_path = os.path.join(cluster_dir, "auth")
        _cluster["auth-dir"] = auth_path
        Path(auth_path).mkdir(parents=True, exist_ok=True)
    return clusters


def abort_no_ocm_token(ocm_token):
    if not ocm_token:
        click.echo("--ocm-token is required for managed cluster")
        raise click.Abort()


def verify_processes_passed(processes, action):
    failed_processes = {}

    for _proc in processes:
        _proc.join()
        if _proc.exitcode != 0:
            failed_processes[_proc.name] = _proc.exitcode

    if failed_processes:
        click.echo(f"Some jobs failed to {action}: {failed_processes}\n")
        raise click.Abort()


def create_openshift_cluster(cluster_data, s3_bucket_name=None, s3_bucket_path=None):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        create_or_destroy_aws_ipi_cluster(
            cluster_data=cluster_data,
            action="create",
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        rosa_create_cluster(
            cluster_data=cluster_data,
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )


def destroy_openshift_cluster(cluster_data):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        create_or_destroy_aws_ipi_cluster(cluster_data=cluster_data, action="destroy")

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        rosa_delete_cluster(cluster_data=cluster_data)


def check_existing_clusters(clusters, ocm_client):
    deployed_clusters_names = {
        cluster["name"]
        for cluster in rosa.cli.execute(
            command="list clusters", aws_region="us-west-2", ocm_client=ocm_client
        )["out"]
    }
    requested_clusters_name = {cluster["name"] for cluster in clusters}
    duplicate_cluster_names = deployed_clusters_names.intersection(
        requested_clusters_name
    )
    if duplicate_cluster_names:
        click.echo(f"At least one cluster name duplication: {duplicate_cluster_names}")
        raise click.Abort()


@click.command()
@click.option(
    "-a",
    "--action",
    type=click.Choice(["create", "destroy"]),
    help="Action to perform Openshift cluster/s",
    required=True,
)
@click.option(
    "-p",
    "--parallel",
    help="Run clusters install/uninstall in parallel",
    is_flag=True,
    show_default=True,
)
@click.option(
    "--ssh-key-file",
    help="id_rsa.pub file path for AWS IPI clusters",
    default="/openshift-cli-installer/ssh-key/id_rsa.pub",
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--clusters-install-data-directory",
    help="""
\b
Path to cluster install data.
    For install this will be used to store the install data.
    For uninstall this will be used to uninstall the cluster.
    Also used to store clusters kubeconfig
""",
    default=os.environ.get(
        "CLUSTER_INSTALL_DATA_DIRECTORY",
        "/openshift-cli-installer/clusters-install-data",
    ),
    type=click.Path(),
    show_default=True,
    required=True,
)
@click.option(
    "--registry-config-file",
    help="""
    \b
registry-config file, can be obtained from https://console.redhat.com/openshift/create/local.
(Needed only for AWS IPI clusters)
    """,
    default=os.environ.get("PULL_SECRET"),
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--s3-bucket-name",
    help="S3 bucket name to store install folder backups",
    show_default=True,
)
@click.option(
    "--s3-bucket-path",
    help="S3 bucket path to store the backups",
    show_default=True,
)
@click.option(
    "--ocm-env",
    help="OCM env to log in into. Needed for managed AWS cluster",
    type=click.Choice(["stage", "production"]),
    default="stage",
    show_default=True,
)
@click.option(
    "--ocm-token",
    help="OCM token, needed for managed AWS cluster.",
    default=os.environ.get("OCM_TOKEN"),
)
@click.option(
    "-c",
    "--cluster",
    type=DictParamType(),
    help="""
\b
Cluster/s to install.
Format to pass is:
    'name=cluster1;base_domain=aws.domain.com;platform=aws;region=us-east-2;version=4.14.0-ec.2'
Required parameters:
    name: Cluster name.
    base_domain: Base domain for the cluster.
    platform: Cloud platform to install the cluster on, supported platforms are: aws, rosa and hypershift.
    region: Region to use for the cloud platform.
    version: Openshift cluster version to install
\b
Check install-config-template.j2 for variables that can be overwritten by the user.
For example:
    fips=true
    worker_flavor=m5.xlarge
    worker_replicas=6
    """,
    required=True,
    multiple=True,
)
def main(
    action,
    registry_config_file,
    parallel,
    cluster,
    clusters_install_data_directory,
    s3_bucket_name,
    s3_bucket_path,
    ocm_token,
    ocm_env,
    ssh_key_file,
):
    """
    Create/Destroy Openshift cluster/s
    """
    is_platform_supported(clusters=cluster)
    create = action == "create"
    ocm_client = None
    kwargs = {}

    aws_ipi_clusters, rosa_clusters, hypershift_clusters = get_clusters_by_type(
        clusters=cluster
    )
    if hypershift_clusters or rosa_clusters:
        ocm_client = get_ocm_client(ocm_token=ocm_token, ocm_env=ocm_env)
        if create:
            check_existing_clusters(clusters=cluster, ocm_client=ocm_client)

    if hypershift_clusters:
        is_region_support_hypershift(
            ocm_client=ocm_client,
            hypershift_clusters=hypershift_clusters,
        )

    aws_managed_clusters = rosa_clusters + hypershift_clusters
    if aws_ipi_clusters or aws_managed_clusters:
        _regions_to_verify = set()
        for _cluster in aws_ipi_clusters + aws_managed_clusters:
            _regions_to_verify.add(_cluster["region"])

        for _region in _regions_to_verify:
            set_and_verify_aws_credentials(region_name=_region)

    if aws_ipi_clusters:
        aws_ipi_clusters = generate_cluster_dirs_path(
            clusters=aws_ipi_clusters, base_directory=clusters_install_data_directory
        )

        aws_ipi_clusters = download_openshift_install_binary(
            clusters=aws_ipi_clusters, registry_config_file=registry_config_file
        )
        if create:
            aws_ipi_clusters = create_install_config_file(
                clusters=aws_ipi_clusters,
                registry_config_file=registry_config_file,
                ssh_key_file=ssh_key_file,
            )

    if aws_managed_clusters:
        abort_no_ocm_token(ocm_token)
        aws_managed_clusters = generate_cluster_dirs_path(
            clusters=aws_managed_clusters,
            base_directory=clusters_install_data_directory,
        )
        aws_managed_clusters = prepare_managed_clusters_data(
            clusters=aws_managed_clusters,
            ocm_token=ocm_token,
            ocm_env=ocm_env,
        )

    if create:
        kwargs.update(
            {"s3_bucket_name": s3_bucket_name, "s3_bucket_path": s3_bucket_path}
        )

    processes = []
    action_func = create_openshift_cluster if create else destroy_openshift_cluster

    for _cluster in aws_ipi_clusters + aws_managed_clusters:
        kwargs["cluster_data"] = _cluster
        if parallel:
            proc = multiprocessing.Process(
                name=f"{_cluster['name']}---{action}",
                target=action_func,
                kwargs=kwargs,
            )
            processes.append(proc)
            proc.start()

        else:
            action_func(**kwargs)

    if processes:
        verify_processes_passed(processes=processes, action=action)


if __name__ == "__main__":
    main()
