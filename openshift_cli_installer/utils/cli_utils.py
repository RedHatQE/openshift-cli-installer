import ast
import contextlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
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
    SUPPORTED_PLATFORMS,
    TIMEOUT_60MIN,
    USER_INPUT_CLUSTER_BOOLEAN_KEYS,
)
from openshift_cli_installer.utils.general import delete_cluster_s3_buckets, tts

LOGGER = get_logger(name=__name__)


def get_clusters_by_type(clusters):
    clusters_dict = {}
    for platform in SUPPORTED_PLATFORMS:
        clusters_dict[platform] = [
            _cluster for _cluster in clusters if _cluster["platform"] == platform
        ]

    return clusters_dict


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

    delete_cluster_s3_buckets(cluster_data=cluster_data)


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
        (
            aws_access_key_id,
            aws_secret_access_key,
        ) = get_aws_credentials_for_acm_observability(
            cluster=_cluster,
            aws_access_key_id=kwargs.get("aws_access_key_id"),
            aws_secret_access_key=kwargs.get("aws_secret_access_key"),
        )
        _cluster["aws-access-key-id"] = aws_access_key_id
        _cluster["aws-secret-access-key"] = aws_secret_access_key

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


def save_kubeadmin_token_to_clusters_install_data(clusters):
    # Do not run this function in parallel, get_kubeadmin_token() do `oc login`.
    with change_home_environment_on_openshift_ci():
        for cluster_data in clusters:
            with get_kubeadmin_token(cluster_data=cluster_data) as kubeadmin_token:
                cluster_data["kubeadmin-token"] = kubeadmin_token

            dump_cluster_data_to_file(cluster_data=cluster_data)

    return clusters


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


def get_aws_credentials_for_acm_observability(
    cluster, aws_access_key_id, aws_secret_access_key
):
    _aws_access_key_id = cluster.get(
        "acm-observability-s3-access-key-id", aws_access_key_id
    )
    _aws_secret_access_key = cluster.get(
        "acm-observability-s3-secret-access-key", aws_secret_access_key
    )
    return _aws_access_key_id, _aws_secret_access_key
