import ast
import contextlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import rosa.cli
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from ocm_python_wrapper.cluster import Cluster
from ocp_resources.utils import TimeoutWatch
from simple_logger.logger import get_logger

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
    aws_ipi_create_cluster,
    aws_ipi_destroy_cluster,
    create_install_config_file,
    download_openshift_install_binary,
    update_aws_clusters_versions,
)
from openshift_cli_installer.utils.clusters import (
    dump_cluster_data_to_file,
    get_kubeadmin_token,
    get_ocm_client,
    update_rosa_osd_clusters_versions,
)
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    ERROR_LOG_COLOR,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    OCM_MANAGED_PLATFORMS,
    PRODUCTION_STR,
    ROSA_STR,
    STAGE_STR,
    SUCCESS_LOG_COLOR,
    SUPPORTED_ACTIONS,
    SUPPORTED_PLATFORMS,
    TIMEOUT_60MIN,
    USER_INPUT_CLUSTER_BOOLEAN_KEYS,
)
from openshift_cli_installer.utils.gcp import get_gcp_regions
from openshift_cli_installer.utils.general import tts

LOGGER = get_logger(name=__name__)


def get_clusters_by_type(clusters):
    clusters_dict = {}
    for platform in SUPPORTED_PLATFORMS:
        clusters_dict[platform] = [
            _cluster for _cluster in clusters if _cluster["platform"] == platform
        ]

    return clusters_dict


def is_platform_supported(clusters):
    unsupported_platforms = []
    for _cluster in clusters:
        _platform = _cluster["platform"]
        if _platform not in SUPPORTED_PLATFORMS:
            unsupported_platforms.append(
                f"Cluster {_cluster['name']} platform '{_platform}' is not supported.\n"
            )

    if unsupported_platforms:
        click.secho(
            unsupported_platforms,
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
    if hypershift_clusters:
        click.echo(f"Check if regions are {HYPERSHIFT_STR}-supported.")
        hypershift_regions_dict = {PRODUCTION_STR: None, STAGE_STR: None}
        unsupported_regions = []
        for _cluster in hypershift_clusters:
            cluster_ocm_env = _cluster["ocm-env"]
            _hypershift_regions = hypershift_regions_dict[cluster_ocm_env]
            if not _hypershift_regions:
                _hypershift_regions = hypershift_regions(
                    ocm_client=_cluster["ocm-client"]
                )
                hypershift_regions_dict[cluster_ocm_env] = _hypershift_regions

            _region = _cluster["region"]
            if _region not in _hypershift_regions:
                unsupported_regions.append(
                    f"Cluster {_cluster['name']}, region: {_region}\n"
                )

            if unsupported_regions:
                click.secho(
                    f"The following {HYPERSHIFT_STR} clusters regions are no supported:"
                    f" {unsupported_regions}.\nSupported hypershift regions are:"
                    f" {_hypershift_regions}",
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


def create_openshift_cluster(cluster_data, must_gather_output_dir=None):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        return aws_ipi_create_cluster(
            cluster_data=cluster_data, must_gather_output_dir=must_gather_output_dir
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        return rosa_create_cluster(
            cluster_data=cluster_data, must_gather_output_dir=must_gather_output_dir
        )
    elif cluster_platform in (AWS_OSD_STR, GCP_OSD_STR):
        return osd_create_cluster(
            cluster_data=cluster_data, must_gather_output_dir=must_gather_output_dir
        )


def destroy_openshift_cluster(cluster_data):
    cluster_platform = cluster_data["platform"]
    if cluster_platform == AWS_STR:
        return aws_ipi_destroy_cluster(
            cluster_data=cluster_data,
        )

    elif cluster_platform in (ROSA_STR, HYPERSHIFT_STR):
        return rosa_delete_cluster(cluster_data=cluster_data)

    elif cluster_platform in (AWS_OSD_STR, GCP_OSD_STR):
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


def verify_user_input(**kwargs):
    action = kwargs.get("action")
    clusters = kwargs.get("clusters")
    ssh_key_file = kwargs.get("ssh_key_file")
    private_ssh_key_file = kwargs.get("private_ssh_key_file")
    docker_config_file = kwargs.get("docker_config_file")
    registry_config_file = kwargs.get("registry_config_file")
    aws_access_key_id = kwargs.get("aws_access_key_id")
    aws_secret_access_key = kwargs.get("aws_secret_access_key")
    aws_account_id = kwargs.get("aws_account_id")
    ocm_token = kwargs.get("ocm_token")
    destroy_clusters_from_s3_config_files = kwargs.get(
        "destroy_clusters_from_s3_config_files"
    )
    s3_bucket_name = kwargs.get("s3_bucket_name")
    gcp_service_account_file = kwargs.get("gcp_service_account_file")
    create = kwargs.get("create")

    abort_no_ocm_token(ocm_token=ocm_token)

    section = "Verify user input"
    no_platform_no_cluster_for_log = "All"

    if destroy_clusters_from_s3_config_files:
        if not s3_bucket_name:
            click.secho(
                "`--s3-bucket-name` must be provided when running with"
                " `--destroy-clusters-from-s3-config-files`",
                fg=ERROR_LOG_COLOR,
            )
            raise click.Abort()

    else:
        if not action:
            click_echo(
                name=no_platform_no_cluster_for_log,
                platform=no_platform_no_cluster_for_log,
                section=section,
                msg=(
                    "'action' must be provided, supported actions:"
                    f" `{SUPPORTED_ACTIONS}`"
                ),
                error=True,
            )
            raise click.Abort()

        if action not in SUPPORTED_ACTIONS:
            click_echo(
                name=no_platform_no_cluster_for_log,
                platform=no_platform_no_cluster_for_log,
                section=section,
                msg=(
                    f"'{action}' is not supported, supported actions:"
                    f" `{SUPPORTED_ACTIONS}`"
                ),
                error=True,
            )
            raise click.Abort()

        if not clusters:
            click.secho(
                "At least one '--cluster' option must be provided.", fg=ERROR_LOG_COLOR
            )
            raise click.Abort()

        is_platform_supported(clusters=clusters)
        assert_unique_cluster_names(clusters=clusters)
        assert_managed_acm_clusters_user_input(clusters=clusters, create=create)

        assert_aws_ipi_user_input(
            clusters=clusters,
            ssh_key_file=ssh_key_file,
            docker_config_file=docker_config_file,
            registry_config_file=registry_config_file,
        )
        assert_aws_osd_user_input(
            clusters=clusters,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_account_id=aws_account_id,
        )
        assert_acm_clusters_user_input(
            create=create,
            clusters=clusters,
            ssh_key_file=ssh_key_file,
            private_ssh_key_file=private_ssh_key_file,
            registry_config_file=registry_config_file,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        assert_gcp_osd_user_input(
            clusters=clusters,
            gcp_service_account_file=gcp_service_account_file,
            create=create,
        )
        assert_boolean_values(clusters=clusters, create=create)


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


def assert_aws_osd_user_input(
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
    create,
    clusters,
    ssh_key_file,
    private_ssh_key_file,
    registry_config_file,
    aws_access_key_id,
    aws_secret_access_key,
):
    supported_platforms = (ROSA_STR, AWS_STR, AWS_OSD_STR)
    acm_clusters = [_cluster for _cluster in clusters if _cluster.get("acm") is True]
    if acm_clusters and create:
        for _cluster in acm_clusters:
            cluster_platform = _cluster["platform"]
            if cluster_platform not in supported_platforms:
                click_echo(
                    name=_cluster["name"],
                    platform=cluster_platform,
                    section="verify_user_input",
                    msg=(
                        f"ACM not supported for {cluster_platform} clusters, supported"
                        f" platforms are: {supported_platforms}"
                    ),
                    error=True,
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
                _cluster for _cluster in aws_ipi_clusters if _cluster.get("acm") is True
            ]
            for _acm_cluster in acm_clusters:
                _acm_cluster["aws-access-key-id"] = aws_access_key_id
                _acm_cluster["aws-secret-access-key"] = aws_secret_access_key

    return aws_ipi_clusters


def prepare_ocm_managed_clusters(
    osd_managed_clusters,
    clusters_install_data_directory,
    aws_access_key_id,
    aws_secret_access_key,
    aws_account_id,
    create,
    gcp_service_account_file,
):
    if osd_managed_clusters:
        osd_managed_clusters = generate_cluster_dirs_path(
            clusters=osd_managed_clusters,
            base_directory=clusters_install_data_directory,
        )
        osd_managed_clusters = prepare_managed_clusters_data(
            clusters=osd_managed_clusters,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_account_id=aws_account_id,
            gcp_service_account_file=gcp_service_account_file,
            create=create,
        )
        if create:
            osd_managed_clusters = update_rosa_osd_clusters_versions(
                clusters=osd_managed_clusters,
            )

    return osd_managed_clusters


def run_create_or_destroy_clusters(
    clusters, create, action, parallel, must_gather_output_dir
):
    futures = []
    action_func = create_openshift_cluster if create else destroy_openshift_cluster
    processed_clusters = []
    action_kwargs = {}

    if create and must_gather_output_dir:
        action_kwargs["must_gather_output_dir"] = must_gather_output_dir

    with ThreadPoolExecutor() as executor:
        for cluster_data in clusters:
            cluster_data["timeout-watch"] = TimeoutWatch(
                timeout=cluster_data["timeout"]
            )
            _cluster_name = cluster_data["name"]
            action_kwargs["cluster_data"] = cluster_data
            click.echo(
                f"Executing {action} cluster {_cluster_name} [parallel: {parallel}]"
            )
            if parallel:
                futures.append(executor.submit(action_func, **action_kwargs))
            else:
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


def is_region_support_gcp(gcp_osd_clusters, gcp_service_account_file):
    if gcp_osd_clusters:
        click.echo("Check if regions are GCP-supported.")
        supported_regions = get_gcp_regions(gcp_service_account_file)
        unsupported_regions = []
        for cluster_data in gcp_osd_clusters:
            cluster_region = cluster_data["region"]
            if cluster_region not in supported_regions:
                unsupported_regions.append(
                    f"cluster: {cluster_data['name']}, region: {cluster_region}"
                )

        if unsupported_regions:
            click.secho(
                "The following clusters regions are not supported in GCP:"
                f" {unsupported_regions}",
                fg=ERROR_LOG_COLOR,
            )
            raise click.Abort()


def is_region_support_aws(clusters):
    if clusters:
        click.echo(f"Check if regions are {AWS_STR}-supported.")
        _regions_to_verify = set()
        for cluster_data in clusters:
            _regions_to_verify.add(cluster_data["region"])

        for _region in _regions_to_verify:
            set_and_verify_aws_credentials(region_name=_region)


def assert_gcp_osd_user_input(create, clusters, gcp_service_account_file):
    if (
        create
        and any([cluster["platform"] == GCP_OSD_STR for cluster in clusters])
        and not gcp_service_account_file
    ):
        click.secho(
            "`--gcp-service-account-file` option must be provided for"
            f" {GCP_OSD_STR} clusters",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()


def prepare_clusters(clusters, ocm_token):
    supported_envs = (PRODUCTION_STR, STAGE_STR)
    for _cluster in clusters:
        name = _cluster["name"]
        platform = _cluster["platform"]
        _cluster["timeout"] = tts(ts=_cluster.get("timeout", TIMEOUT_60MIN))
        if platform == AWS_STR:
            ocm_env = PRODUCTION_STR
        else:
            ocm_env = _cluster.get("ocm-env", STAGE_STR)
        _cluster["ocm-env"] = ocm_env

        if ocm_env not in supported_envs:
            click.secho(
                f"{name} got unsupported OCM env - {ocm_env}, supported"
                f" envs: {supported_envs}"
            )
            raise click.Abort()

        ocm_client = get_ocm_client(ocm_token=ocm_token, ocm_env=ocm_env)
        _cluster["ocm-client"] = ocm_client
        if platform in OCM_MANAGED_PLATFORMS:
            _cluster["cluster-object"] = Cluster(
                client=ocm_client,
                name=name,
            )

    return clusters


def click_echo(name, platform, section, msg, success=None, error=None):
    if success:
        fg = SUCCESS_LOG_COLOR
    elif error:
        fg = ERROR_LOG_COLOR
    else:
        fg = "white"

    click.secho(
        f"[Cluster: {name} - Platform: {platform} - Section: {section}]: {msg}", fg=fg
    )


def assert_managed_acm_clusters_user_input(clusters, create):
    if create:
        section = "Verify user input"
        for cluster in clusters:
            managed_acm_clusters = get_managed_acm_clusters_from_user_input(
                cluster=cluster
            )
            for managed_acm_cluster in managed_acm_clusters:
                managed_acm_cluster_data = get_cluster_data_by_name_from_clusters(
                    name=managed_acm_cluster, clusters=clusters
                )
                if not managed_acm_cluster_data:
                    click_echo(
                        name=managed_acm_cluster,
                        platform=None,
                        section=section,
                        error=True,
                        msg=f"Cluster {managed_acm_cluster} not found",
                    )
                    raise click.Abort()


def get_managed_acm_clusters_from_user_input(cluster):
    managed_acm_clusters = cluster.get("acm-clusters")

    # When user input is a single string, we need to convert it to a list
    # Single string will be when user send only one cluster: acm-clusters=cluster1
    managed_acm_clusters = (
        managed_acm_clusters
        if isinstance(managed_acm_clusters, list)
        else [managed_acm_clusters]
    )

    # Filter all `None` objects from the list
    return [_cluster for _cluster in managed_acm_clusters if _cluster]


def get_clusters_from_user_input(**kwargs):
    # From CLI, we get `cluster`, from YAML file we get `clusters`
    clusters = kwargs.get("cluster", [])
    if not clusters:
        clusters = kwargs.get("clusters", [])

    for _cluster in clusters:
        for key in USER_INPUT_CLUSTER_BOOLEAN_KEYS:
            cluster_key_value = _cluster.get(key)
            if cluster_key_value and isinstance(cluster_key_value, str):
                try:
                    _cluster[key] = ast.literal_eval(cluster_key_value)
                except ValueError:
                    continue

    return clusters


def get_cluster_data_by_name_from_clusters(name, clusters):
    for cluster in clusters:
        if cluster["name"] == name:
            return cluster


def assert_unique_cluster_names(clusters):
    cluster_names = [cluster["name"] for cluster in clusters]
    if len(cluster_names) != len(set(cluster_names)):
        click_echo(
            name=None,
            platform="All",
            section="verify_user_input",
            error=True,
            msg=f"Cluster names must be unique: clusters {cluster_names}",
        )
        raise click.Abort()


def save_kubeadmin_token_to_clusters_install_data(clusters):
    # Do not run this function in parallel, get_kubeadmin_token() do `oc login`.
    with change_home_environment_on_openshift_ci():
        for cluster_data in clusters:
            with get_kubeadmin_token(cluster_data=cluster_data) as kubeadmin_token:
                cluster_data["kubeadmin-token"] = kubeadmin_token

            dump_cluster_data_to_file(cluster_data=cluster_data)

    return clusters


def assert_boolean_values(clusters, create):
    if create:
        for cluster in clusters:
            non_bool_keys = [
                cluster_data_key
                for cluster_data_key, cluster_data_value in cluster.items()
                if cluster_data_key in USER_INPUT_CLUSTER_BOOLEAN_KEYS
                and not isinstance(cluster_data_value, bool)
            ]
            if non_bool_keys:
                click_echo(
                    name=cluster["name"],
                    platform=cluster["platform"],
                    section="verify_user_input",
                    error=True,
                    msg=f"The following keys must be booleans: {non_bool_keys}",
                )
                raise click.Abort()


@contextlib.contextmanager
def change_home_environment_on_openshift_ci():
    home_str = "HOME"
    current_home = os.environ.get(home_str)
    run_in_openshift_ci = os.environ.get("OPENSHIFT_CI") == "true"
    # If running on openshift-ci we need to change $HOME to /tmp
    if run_in_openshift_ci:
        LOGGER.info("Running in openshift ci")
        tmp_home_dir = "/tmp/"
        LOGGER.info(f"Changing {home_str} environment variable to {tmp_home_dir}")
        os.environ[home_str] = tmp_home_dir
        yield
    else:
        yield

    if run_in_openshift_ci:
        LOGGER.info(
            f"Changing {home_str} environment variable to previous value."
            f" {current_home}"
        )
        os.environ[home_str] = current_home
