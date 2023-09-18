import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import rosa.cli
from ocp_resources.utils import TimeoutWatch

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
from openshift_cli_installer.utils.clusters import update_rosa_osd_clusters_versions
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    CREATE_STR,
    DESTROY_STR,
    ERROR_LOG_COLOR,
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
                fg=ERROR_LOG_COLOR,
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
                fg=ERROR_LOG_COLOR,
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
        click.secho("--ocm-token is required for clusters", fg=ERROR_LOG_COLOR)
        raise click.Abort()


def verify_processes_passed(processes, action):
    failed_processes = {}

    for _proc in processes:
        _proc.join()
        if _proc.exitcode != 0:
            failed_processes[_proc.name] = _proc.exitcode

    if failed_processes:
        click.secho(
            f"Some jobs failed to {action}: {failed_processes}\n", fg=ERROR_LOG_COLOR
        )
        raise click.Abort()


def create_openshift_cluster(
    cluster_data,
):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        return create_or_destroy_aws_ipi_cluster(
            cluster_data=cluster_data,
            action=CREATE_STR,
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        return rosa_create_cluster(
            cluster_data=cluster_data,
        )
    elif cluster_platform == AWS_OSD_STR:
        return osd_create_cluster(cluster_data=cluster_data)


def destroy_openshift_cluster(cluster_data):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        return create_or_destroy_aws_ipi_cluster(
            cluster_data=cluster_data, action=DESTROY_STR
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        return rosa_delete_cluster(cluster_data=cluster_data)

    elif cluster_platform == AWS_OSD_STR:
        return osd_delete_cluster(cluster_data=cluster_data)


def assert_public_ssh_key_file_exists(ssh_key_file):
    if not ssh_key_file or not os.path.exists(ssh_key_file):
        click.secho(
            "SSH file is required for AWS or ACM cluster installations."
            f" {ssh_key_file} file does not exist.",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()


def assert_registry_config_file_exists(registry_config_file):
    if not registry_config_file or not os.path.exists(registry_config_file):
        click.secho(
            "Registry config file is required for AWS or ACM cluster installations."
            f" {registry_config_file} file does not exist.",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()


def assert_aws_credentials_exist(aws_access_key_id, aws_secret_access_key):
    if not (aws_secret_access_key and aws_access_key_id):
        click.secho(
            "--aws-secret-access-key and aws-access-key-id"
            " required for AWS OSD OR ACM cluster installations.",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()


def verify_user_input(
    action,
    clusters,
    ssh_key_file,
    private_ssh_key_file,
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
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()

    if not clusters:
        click.secho(
            "At least one '--cluster' option must be provided.", fg=ERROR_LOG_COLOR
        )
        raise click.Abort()

    abort_no_ocm_token(ocm_token=ocm_token)
    is_platform_supported(clusters=clusters)
    assert_aws_ipi_user_input(
        clusters=clusters,
        ssh_key_file=ssh_key_file,
        docker_config_file=docker_config_file,
        registry_config_file=registry_config_file,
    )
    assert_osd_user_input(
        clusters=clusters,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_account_id=aws_account_id,
    )
    assert_acm_clusters_user_input(
        action=action,
        clusters=clusters,
        ssh_key_file=ssh_key_file,
        private_ssh_key_file=private_ssh_key_file,
        registry_config_file=registry_config_file,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )


def assert_aws_ipi_user_input(
    clusters, ssh_key_file, docker_config_file, registry_config_file
):
    if any([_cluster["platform"] == AWS_STR for _cluster in clusters]):
        if not docker_config_file or not os.path.exists(docker_config_file):
            click.secho(
                "Docker config file is required for AWS installations."
                f" {docker_config_file} file does not exist.",
                fg=ERROR_LOG_COLOR,
            )
            raise click.Abort()

        assert_public_ssh_key_file_exists(ssh_key_file=ssh_key_file)
        assert_registry_config_file_exists(registry_config_file=registry_config_file)


def assert_osd_user_input(
    clusters, aws_access_key_id, aws_secret_access_key, aws_account_id
):
    if any([_cluster["platform"] == AWS_OSD_STR for _cluster in clusters]):
        assert_aws_credentials_exist(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        if not aws_account_id:
            click.secho(
                "--aws-account_id required for AWS OSD installations.",
                fg=ERROR_LOG_COLOR,
            )
            raise click.Abort()


def assert_acm_clusters_user_input(
    action,
    clusters,
    ssh_key_file,
    private_ssh_key_file,
    registry_config_file,
    aws_access_key_id,
    aws_secret_access_key,
):
    acm_clusters = [_cluster for _cluster in clusters if _cluster.get("acm")]
    if acm_clusters and action == CREATE_STR:
        if any([_cluster["platform"] == HYPERSHIFT_STR for _cluster in acm_clusters]):
            click.secho(
                f"ACM not supported for {HYPERSHIFT_STR} clusters", fg=ERROR_LOG_COLOR
            )
            raise click.Abort()

        assert_public_ssh_key_file_exists(ssh_key_file=ssh_key_file)
        assert_registry_config_file_exists(registry_config_file=registry_config_file)
        assert_aws_credentials_exist(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        if not private_ssh_key_file or not os.path.exists(private_ssh_key_file):
            click.secho(
                "SSH private file is required for ACM cluster installations."
                f" {private_ssh_key_file} file does not exist.",
                fg=ERROR_LOG_COLOR,
            )
            raise click.Abort()


def destroy_s3_or_all_clusters(
    destroy_clusters_from_s3_config_files,
    s3_bucket_name,
    s3_bucket_path,
    clusters_install_data_directory,
    registry_config_file,
    destroy_all_clusters,
    ocm_token,
):
    if destroy_clusters_from_s3_config_files and not s3_bucket_name:
        click.secho(
            "`--s3-bucket-name` must be provided when running with"
            " `--destroy-clusters-from-s3-config-files`",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()

    if destroy_all_clusters or destroy_clusters_from_s3_config_files:
        return destroy_clusters(
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
            clusters_install_data_directory=clusters_install_data_directory,
            registry_config_file=registry_config_file,
            clusters_yaml_files=destroy_clusters_from_s3_config_files,
            destroy_all_clusters=destroy_all_clusters,
            ocm_token=ocm_token,
        )


def prepare_aws_ipi_clusters(
    aws_ipi_clusters,
    clusters_install_data_directory,
    registry_config_file,
    ssh_key_file,
    docker_config_file,
    create,
    aws_access_key_id,
    aws_secret_access_key,
):
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
            acm_clusters = [
                _cluster for _cluster in aws_ipi_clusters if _cluster.get("acm")
            ]
            for _acm_cluster in acm_clusters:
                _acm_cluster["aws-access-key-id"] = aws_access_key_id
                _acm_cluster["aws-secret-access-key"] = aws_secret_access_key

    return aws_ipi_clusters


def prepare_aws_managed_clusters(
    aws_managed_clusters,
    clusters_install_data_directory,
    aws_access_key_id,
    aws_secret_access_key,
    aws_account_id,
    create,
):
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

    return aws_managed_clusters


def run_create_or_destroy_clusters(clusters, create, action, parallel):
    futures = []
    action_func = create_openshift_cluster if create else destroy_openshift_cluster
    processed_clusters = []

    with ThreadPoolExecutor() as executor:
        for cluster_data in clusters:
            _cluster_name = cluster_data["name"]
            action_kwargs = {"cluster_data": cluster_data}
            click.echo(
                f"Executing {action} cluster {_cluster_name} [parallel: {parallel}]"
            )
            if parallel:
                futures.append(executor.submit(action_func, **action_kwargs))
            else:
                cluster_data["timeout-watch"] = TimeoutWatch(
                    timeout=cluster_data["timeout"]
                )
                processed_clusters.append(action_func(**action_kwargs))

    if futures:
        for result in as_completed(futures):
            if result.exception():
                click.secho(
                    f"Failed to {action} cluster: {result.exception()}\n",
                    fg=ERROR_LOG_COLOR,
                )
                raise click.Abort()
            processed_clusters.append(result.result())

    return processed_clusters
