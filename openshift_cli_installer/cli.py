import multiprocessing
import os
from pathlib import Path

import click
import rosa.cli
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from pyaml_env import parse_config

from openshift_cli_installer.libs.destroy_clusters import destroy_clusters
from openshift_cli_installer.libs.managed_clusters.helpers import (
    prepare_managed_clusters_data,
)
from openshift_cli_installer.libs.managed_clusters.osd_clusters import (
    osd_create_cluster,
    osd_delete_cluster,
)
from openshift_cli_installer.libs.managed_clusters.rosa_clusters import (
    rosa_create_cluster,
    rosa_delete_cluster,
)
from openshift_cli_installer.libs.unmanaged_clusters.aws_ipi_clusters import (
    create_install_config_file,
    create_or_destroy_aws_ipi_cluster,
    download_openshift_install_binary,
    update_aws_clusters_versions,
)
from openshift_cli_installer.utils.click_dict_type import DictParamType
from openshift_cli_installer.utils.clusters import (
    add_ocm_client_to_cluster_dict,
    check_existing_clusters,
    update_rosa_osd_clusters_versions,
)
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    CLUSTER_DATA_YAML_FILENAME,
    CREATE_STR,
    DESTROY_STR,
    HYPERSHIFT_STR,
    PRODUCTION_STR,
    ROSA_STR,
    STAGE_STR,
)


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
    aws_osd_clusters = [
        _cluster for _cluster in clusters if _cluster["platform"] == AWS_OSD_STR
    ]
    return aws_ipi_clusters, rosa_clusters, hypershift_clusters, aws_osd_clusters


def is_platform_supported(clusters):
    supported_platform = (AWS_STR, ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR)
    for _cluster in clusters:
        _platform = _cluster["platform"]
        if _platform not in supported_platform:
            click.secho(
                f"Cluster platform '{_platform}' is not supported.\n{_cluster}",
                fg="red",
            )
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


def is_region_support_hypershift(hypershift_clusters):
    hypershift_regions_dict = {PRODUCTION_STR: None, STAGE_STR: None}
    for _cluster in hypershift_clusters:
        cluster_ocm_env = _cluster["ocm-env"]
        _hypershift_regions = hypershift_regions_dict[cluster_ocm_env]
        if not _hypershift_regions:
            _hypershift_regions = hypershift_regions(ocm_client=_cluster["ocm-client"])
            hypershift_regions_dict[cluster_ocm_env] = _hypershift_regions

        _region = _cluster["region"]
        if _region not in _hypershift_regions:
            click.secho(
                f"region '{_region}' does not supported {HYPERSHIFT_STR}."
                f"\nSupported hypershift regions are: {_hypershift_regions}"
                f"\n{_cluster}",
                fg="red",
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
        click.secho("--ocm-token is required for clusters", fg="red")
        raise click.Abort()


def verify_processes_passed(processes, action):
    failed_processes = {}

    for _proc in processes:
        _proc.join()
        if _proc.exitcode != 0:
            failed_processes[_proc.name] = _proc.exitcode

    if failed_processes:
        click.secho(f"Some jobs failed to {action}: {failed_processes}\n", fg="red")
        raise click.Abort()


def create_openshift_cluster(
    cluster_data,
    s3_bucket_name=None,
    s3_bucket_path=None,
):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        create_or_destroy_aws_ipi_cluster(
            cluster_data=cluster_data,
            action=CREATE_STR,
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        rosa_create_cluster(
            cluster_data=cluster_data,
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )
    elif cluster_platform == AWS_OSD_STR:
        osd_create_cluster(cluster_data=cluster_data)


def destroy_openshift_cluster(cluster_data):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        create_or_destroy_aws_ipi_cluster(cluster_data=cluster_data, action=DESTROY_STR)

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        rosa_delete_cluster(cluster_data=cluster_data)

    elif cluster_platform == AWS_OSD_STR:
        osd_delete_cluster(cluster_data=cluster_data)


def verify_user_input(
    action,
    clusters,
    ssh_key_file,
    docker_config_file,
    registry_config_file,
    aws_access_key_id,
    aws_secret_access_key,
    aws_account_id,
    ocm_token,
):
    if not action:
        click.secho(
            f"'action' must be provided, supported actions: `{CREATE_STR}`,"
            f" `{DESTROY_STR}`",
            fg="red",
        )
        raise click.Abort()

    if not clusters:
        click.secho("At least one '--cluster' option must be provided.", fg="red")
        raise click.Abort()

    if any([_cluster["platform"] == AWS_STR for _cluster in clusters]):
        if not os.path.exists(ssh_key_file):
            click.secho(
                f"SSH file is required for AWS installations. {ssh_key_file} file does"
                " not exist.",
                fg="red",
            )
            raise click.Abort()

        if not os.path.exists(docker_config_file):
            click.secho(
                "Docker config file is required for AWS installations."
                f" {docker_config_file} file does not exist.",
                fg="red",
            )
            raise click.Abort()

        if not registry_config_file or not os.path.exists(registry_config_file):
            click.secho(
                "Registry config file is required for AWS installations."
                f" {registry_config_file} file does not exist.",
                fg="red",
            )
            raise click.Abort()

    if any([_cluster["platform"] == AWS_OSD_STR for _cluster in clusters]):
        if not (aws_account_id and aws_secret_access_key and aws_access_key_id):
            click.secho(
                "--aws-account_id and --aws-secret-access-key and aws-access-key-id"
                " required for AWS OSD installations.",
                fg="red",
            )
            raise click.Abort()

    is_platform_supported(clusters=clusters)
    abort_no_ocm_token(ocm_token=ocm_token)


@click.command("installer")
@click.option(
    "-a",
    "--action",
    type=click.Choice([CREATE_STR, DESTROY_STR]),
    help="Action to perform Openshift cluster/s",
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
    type=click.Path(),
    show_default=True,
)
@click.option(
    "--clusters-install-data-directory",
    help="""
\b
Path to clusters install data.
    For install this will be used to store the install data.
    For uninstall this will be used to uninstall the clusters.
    Also used to store clusters kubeconfig.
    Default: "/openshift-cli-installer/clusters-install-data"
""",
    default=os.environ.get(
        "CLUSTER_INSTALL_DATA_DIRECTORY",
    ),
    type=click.Path(),
    show_default=True,
)
@click.option(
    "--registry-config-file",
    help="""
    \b
registry-config file, can be obtained from https://console.redhat.com/openshift/create/local.
(Needed only for AWS IPI clusters)
    """,
    default=os.environ.get("PULL_SECRET"),
    type=click.Path(),
    show_default=True,
)
@click.option(
    "--docker-config-file",
    type=click.Path(),
    default=os.path.expanduser("~/.docker/config.json"),
    help="""
    \b
Path to Docker config.json file.
File must include token for `registry.ci.openshift.org`
(Needed only for AWS IPI clusters)
    """,
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
    "--ocm-token",
    help="OCM token.",
    default=os.environ.get("OCM_TOKEN"),
)
@click.option(
    "--aws-access-key-id",
    help="AWS access-key-id, needed for OSD AWS clusters.",
    default=os.environ.get("AWS_ACCESS_KEY_ID"),
)
@click.option(
    "--aws-secret-access-key",
    help="AWS secret-access-key, needed for OSD AWS clusters.",
    default=os.environ.get("AWS_SECRET_ACCESS_KEY"),
)
@click.option(
    "--aws-account-id",
    help="AWS account-id, needed for OSD AWS clusters.",
    default=os.environ.get("AWS_ACCOUNT_ID"),
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
    multiple=True,
)
@click.option(
    "--destroy-all-clusters",
    help="""
\b
Destroy all clusters under `--clusters-install-data-directory` and/or
saved in S3 bucket (`--s3-bucket-path` `--s3-bucket-name`).
S3 objects will be deleted upon successful deletion.
    """,
    is_flag=True,
    show_default=True,
)
@click.option(
    "--destroy-clusters-from-config-files",
    help=f"""
\b
Destroy clusters from a list of paths to `{CLUSTER_DATA_YAML_FILENAME}` files.
The yaml file must include `s3_object_name` with s3 objet name.
`--s3-bucket-name` and optionally `--s3-bucket-path` must be provided.
S3 objects will be deleted upon successful deletion.
For example:
    '/tmp/cluster1/{CLUSTER_DATA_YAML_FILENAME},/tmp/cluster2/{CLUSTER_DATA_YAML_FILENAME}'
    """,
    show_default=True,
)
@click.option(
    "--clusters-yaml-config-file",
    help="""
    \b
    Yaml file with configuration to create clusters, when using YAML file all other user options are ignored
    """,
    type=click.Path(exists=True),
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
    ssh_key_file,
    destroy_all_clusters,
    destroy_clusters_from_config_files,
    docker_config_file,
    aws_access_key_id,
    aws_secret_access_key,
    aws_account_id,
    clusters_yaml_config_file,
):
    """
    Create/Destroy Openshift cluster/s
    """
    if destroy_clusters_from_config_files and not s3_bucket_name:
        click.secho(
            "`--s3-bucket-name` must be provided when running with"
            " `--destroy-clusters-from-config-files`",
            fg="red",
        )
        raise click.Abort()

    if destroy_all_clusters or destroy_clusters_from_config_files:
        return destroy_clusters(
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
            clusters_install_data_directory=clusters_install_data_directory,
            registry_config_file=registry_config_file,
            clusters_yaml_files=destroy_clusters_from_config_files,
            destroy_all_clusters=destroy_all_clusters,
        )

    if action and clusters_yaml_config_file:
        click.secho("Either send `--clusters-yaml-config-file` or `--action`")
        raise click.Abort()

    clusters = cluster

    if clusters_yaml_config_file:
        yaml_config_data = parse_config(path=clusters_yaml_config_file)
        clusters = yaml_config_data["clusters"]
        parallel = yaml_config_data["parallel"]
        action = yaml_config_data["action"]
        ssh_key_file = yaml_config_data.get("ssh_key_file")
        docker_config_file = yaml_config_data.get("docker_config_file")
        registry_config_file = yaml_config_data.get("registry_config_file")
        aws_access_key_id = yaml_config_data.get("aws_access_key_id")
        aws_secret_access_key = yaml_config_data.get("aws_secret_access_key")
        aws_account_id = yaml_config_data.get("aws_account_id")
        ocm_token = yaml_config_data.get("ocm_token")
        clusters_install_data_directory = yaml_config_data[
            "clusters_install_data_directory"
        ]

    verify_user_input(
        action=action,
        clusters=clusters,
        ssh_key_file=ssh_key_file,
        docker_config_file=docker_config_file,
        registry_config_file=registry_config_file,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_account_id=aws_account_id,
        ocm_token=ocm_token,
    )

    clusters_install_data_directory = (
        clusters_install_data_directory
        or "/openshift-cli-installer/clusters-install-data"
    )

    clusters = add_ocm_client_to_cluster_dict(clusters=clusters, ocm_token=ocm_token)
    create = action == CREATE_STR
    kwargs = {}

    (
        aws_ipi_clusters,
        rosa_clusters,
        hypershift_clusters,
        aws_osd_clusters,
    ) = get_clusters_by_type(clusters=clusters)
    aws_managed_clusters = rosa_clusters + hypershift_clusters + aws_osd_clusters

    if (hypershift_clusters or rosa_clusters or aws_osd_clusters) and create:
        check_existing_clusters(
            clusters=hypershift_clusters + rosa_clusters + aws_osd_clusters
        )

    if hypershift_clusters:
        is_region_support_hypershift(
            hypershift_clusters=hypershift_clusters,
        )

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

        aws_ipi_clusters = update_aws_clusters_versions(
            clusters=aws_ipi_clusters,
        )

        aws_ipi_clusters = download_openshift_install_binary(
            clusters=aws_ipi_clusters, registry_config_file=registry_config_file
        )
        if create:
            aws_ipi_clusters = create_install_config_file(
                clusters=aws_ipi_clusters,
                registry_config_file=registry_config_file,
                ssh_key_file=ssh_key_file,
                docker_config_file=docker_config_file,
            )

    if aws_managed_clusters:
        aws_managed_clusters = generate_cluster_dirs_path(
            clusters=aws_managed_clusters,
            base_directory=clusters_install_data_directory,
        )
        aws_managed_clusters = prepare_managed_clusters_data(
            clusters=aws_managed_clusters,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_account_id=aws_account_id,
        )
        if create:
            aws_managed_clusters = update_rosa_osd_clusters_versions(
                clusters=aws_managed_clusters,
            )

    if create:
        kwargs.update(
            {
                "s3_bucket_name": s3_bucket_name,
                "s3_bucket_path": s3_bucket_path,
            }
        )

    processes = []
    action_func = create_openshift_cluster if create else destroy_openshift_cluster

    for _cluster in aws_ipi_clusters + aws_managed_clusters:
        _cluster_name = _cluster["name"]
        click.echo(f"Executing {action} {_cluster_name} [parallel: {parallel}]")
        kwargs["cluster_data"] = _cluster
        if parallel:
            proc = multiprocessing.Process(
                name=f"{_cluster_name}---{action}",
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
