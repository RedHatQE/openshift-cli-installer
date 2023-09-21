import os

import click
from pyaml_env import parse_config

from openshift_cli_installer.libs.destroy_clusters import destroy_clusters
from openshift_cli_installer.libs.managed_clusters.acm_clusters import (
    install_and_attach_for_acm,
)
from openshift_cli_installer.utils.cli_utils import (
    get_clusters_by_type,
    is_region_support_aws,
    is_region_support_gcp,
    is_region_support_hypershift,
    prepare_aws_ipi_clusters,
    prepare_clusters,
    prepare_ocm_managed_clusters,
    run_create_or_destroy_clusters,
    verify_user_input,
)
from openshift_cli_installer.utils.click_dict_type import DictParamType
from openshift_cli_installer.utils.clusters import (
    add_s3_bucket_data,
    check_ocm_managed_existing_clusters,
)
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    CLUSTER_DATA_YAML_FILENAME,
    CREATE_STR,
    DESTROY_STR,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    ROSA_STR,
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
    "--private-ssh-key-file",
    help="id_rsa file path for ACM clusters",
    default="/openshift-cli-installer/ssh-key/id_rsa",
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
    Yaml file with configuration to create clusters, when using YAML file all other user options are ignored
    except --action which can be send via CLI or in the YAML file.
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
def main(**kwargs):
    """
    Create/Destroy Openshift cluster/s
    """
    user_kwargs = kwargs
    clusters_yaml_config_file = kwargs.get("clusters_yaml_config_file")
    if clusters_yaml_config_file:
        user_kwargs = parse_config(path=clusters_yaml_config_file)

    action = user_kwargs.get("action", kwargs.get("action"))
    clusters = user_kwargs.get("cluster", user_kwargs.get("clusters"))
    ocm_token = user_kwargs.get("ocm_token")
    parallel = False if clusters and len(clusters) == 1 else user_kwargs.get("parallel")
    clusters_install_data_directory = user_kwargs.get(
        "clusters_install_data_directory",
        "/openshift-cli-installer/clusters-install-data",
    )
    destroy_clusters_from_s3_config_files = user_kwargs.get(
        "destroy_clusters_from_s3_config_files"
    )
    s3_bucket_name = user_kwargs.get("s3_bucket_name")
    s3_bucket_path = user_kwargs.get("s3_bucket_path")
    destroy_all_clusters = user_kwargs.get("destroy_all_clusters")
    registry_config_file = user_kwargs.get("registry_config_file")
    ssh_key_file = user_kwargs.get("ssh_key_file")
    private_ssh_key_file = user_kwargs.get("private_ssh_key_file")
    docker_config_file = user_kwargs.get("docker_config_file")
    aws_access_key_id = user_kwargs.get("aws_access_key_id")
    aws_secret_access_key = user_kwargs.get("aws_secret_access_key")
    aws_account_id = user_kwargs.get("aws_account_id")
    gcp_service_account_file = user_kwargs.get("gcp_service_account_file")

    create = action == CREATE_STR
    verify_user_input(
        action=action,
        clusters=clusters,
        ssh_key_file=ssh_key_file,
        private_ssh_key_file=private_ssh_key_file,
        docker_config_file=docker_config_file,
        registry_config_file=registry_config_file,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_account_id=aws_account_id,
        ocm_token=ocm_token,
        destroy_clusters_from_s3_config_files=destroy_clusters_from_s3_config_files,
        s3_bucket_name=s3_bucket_name,
        gcp_service_account_file=gcp_service_account_file,
        create=create,
    )

    if destroy_clusters_from_s3_config_files or destroy_all_clusters:
        return destroy_clusters(
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
            clusters_install_data_directory=clusters_install_data_directory,
            registry_config_file=registry_config_file,
            clusters_dir_paths=destroy_clusters_from_s3_config_files,
            destroy_all_clusters=destroy_all_clusters,
            ocm_token=ocm_token,
            parallel=parallel,
        )

    # General prepare for all clusters
    clusters = prepare_clusters(clusters=clusters, ocm_token=ocm_token)

    if create and s3_bucket_name:
        clusters = add_s3_bucket_data(
            clusters=clusters,
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )

    clusters_dict = get_clusters_by_type(clusters=clusters)
    aws_ipi_clusters = clusters_dict.get(AWS_STR)
    rosa_clusters = clusters_dict.get(ROSA_STR)
    hypershift_clusters = clusters_dict.get(HYPERSHIFT_STR)
    aws_osd_clusters = clusters_dict.get(AWS_OSD_STR)
    gcp_osd_clusters = clusters_dict.get(GCP_OSD_STR)

    aws_managed_clusters = rosa_clusters + hypershift_clusters + aws_osd_clusters
    ocm_managed_clusters = aws_managed_clusters + gcp_osd_clusters

    if create:
        check_ocm_managed_existing_clusters(clusters=ocm_managed_clusters)
        is_region_support_hypershift(hypershift_clusters=hypershift_clusters)
        is_region_support_aws(clusters=aws_ipi_clusters + aws_managed_clusters)
        is_region_support_gcp(
            gcp_osd_clusters=gcp_osd_clusters,
            gcp_service_account_file=gcp_service_account_file,
        )

    aws_ipi_clusters = prepare_aws_ipi_clusters(
        aws_ipi_clusters=aws_ipi_clusters,
        clusters_install_data_directory=clusters_install_data_directory,
        registry_config_file=registry_config_file,
        ssh_key_file=ssh_key_file,
        docker_config_file=docker_config_file,
        create=create,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )
    ocm_managed_clusters = prepare_ocm_managed_clusters(
        osd_managed_clusters=ocm_managed_clusters,
        clusters_install_data_directory=clusters_install_data_directory,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_account_id=aws_account_id,
        create=create,
        gcp_service_account_file=gcp_service_account_file,
    )

    processed_clusters = run_create_or_destroy_clusters(
        clusters=aws_ipi_clusters + ocm_managed_clusters,
        create=create,
        action=action,
        parallel=parallel,
    )

    if create:
        install_and_attach_for_acm(
            managed_clusters=processed_clusters,
            private_ssh_key_file=private_ssh_key_file,
            ssh_key_file=ssh_key_file,
            registry_config_file=registry_config_file,
            clusters_install_data_directory=clusters_install_data_directory,
        )


if __name__ == "__main__":
    main()
