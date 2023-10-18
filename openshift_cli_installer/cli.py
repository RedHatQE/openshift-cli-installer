import datetime
import os
import time

import click
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.clusters.ocp_clusters import OCPClusters
from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.click_dict_type import DictParamType
from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    CREATE_STR,
    DESTROY_STR,
)


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
    help="id_rsa.pub file path for AWS IPI or ACM clusters",
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
    default=os.environ.get("CLUSTER_INSTALL_DATA_DIRECTORY"),
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
    "--destroy-clusters-from-s3-config-files",
    help=f"""
\b
Destroy clusters from a list of paths to `{CLUSTER_DATA_YAML_FILENAME}` files.
The yaml file must include `s3-object-name` with s3 objet name.
`--s3-bucket-name` and optionally `--s3-bucket-path` must be provided.
S3 objects will be deleted upon successful deletion.
For example:
    '/tmp/cluster1/,/tmp/cluster2/'
    """,
    show_default=True,
)
@click.option(
    "--clusters-yaml-config-file",
    help="""
    \b
    YAML file with configuration to create clusters, any option in YAML file will override the CLI option.
    See manifests/clusters.example.yaml for example.
    """,
    type=click.Path(exists=True),
)
@click.option(
    "--gcp-service-account-file",
    help="""
\b
Path to GCP service account json file.
""",
    type=click.Path(exists=True),
)
@click.option(
    "--must-gather-output-dir",
    help="""
\b
Path to must-gather output directory.
must-gather will try to collect data when cluster installation fails and cluster can be accessed.
""",
    type=click.Path(exists=True),
)
@click.option(
    "--dry-run",
    help="For testing, only verify user input",
    is_flag=True,
    show_default=True,
)
def main(**kwargs):
    """
    Create/Destroy Openshift cluster/s
    """
    if kwargs["dry_run"]:
        UserInput(**kwargs)
        return

    # if (
    #     user_input.destroy_clusters_from_s3_config_files
    #     or user_input.destroy_all_clusters
    # ):
    #     return destroy_clusters(
    #         s3_bucket_name=user_input.s3_bucket_name,
    #         s3_bucket_path=user_input.s3_bucket_path,
    #         clusters_install_data_directory=user_input.clusters_install_data_directory,
    #         registry_config_file=user_input.registry_config_file,
    #         clusters_dir_paths=user_input.destroy_clusters_from_s3_config_files,
    #         destroy_all_clusters=user_input.destroy_all_clusters,
    #         ocm_token=user_input.ocm_token,
    #         parallel=user_input.parallel,
    #     )

    # General prepare for all clusters
    clusters = OCPClusters(**kwargs)
    clusters.run_create_or_destroy_clusters()


if __name__ == "__main__":
    start_time = time.time()
    try:
        main()
    finally:
        _logger = get_logger(name="openshift-cli-installer")
        elapsed_time = datetime.timedelta(seconds=time.time() - start_time)
        _logger.info(f"Total execution time: {elapsed_time}")
