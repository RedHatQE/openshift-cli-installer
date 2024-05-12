import shutil
from typing import Any

from openshift_cli_installer.libs.clusters.ocp_clusters import OCPClusters
from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.clusters import destroy_clusters_from_s3_bucket_or_local_directory
from openshift_cli_installer.utils.const import CREATE_STR, DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY
from openshift_cli_installer.utils.gcp_utils import restore_gcp_configuration, set_gcp_configuration


def cli_entrypoint(**kwargs: Any) -> None:
    user_input = UserInput(**kwargs)

    if user_input.dry_run:
        return

    gcp_params = set_gcp_configuration(user_input=user_input)

    try:
        if (
            user_input.destroy_clusters_from_s3_bucket
            or user_input.destroy_clusters_from_install_data_directory
            or user_input.destroy_clusters_from_install_data_directory_using_s3_bucket
            or user_input.destroy_clusters_from_s3_bucket_query
        ):
            user_input.destroy_from_s3_bucket_or_local_directory = True
            user_input = destroy_clusters_from_s3_bucket_or_local_directory(user_input=user_input)

            try:
                clusters = OCPClusters(user_input=user_input)
                clusters.run_create_or_destroy_clusters()
            finally:
                shutil.rmtree(DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY, ignore_errors=True)

        else:
            user_input.destroy_from_s3_bucket_or_local_directory = False
            clusters = OCPClusters(user_input=user_input)
            clusters.run_create_or_destroy_clusters()

            if user_input.action == CREATE_STR:
                clusters.install_acm_on_clusters()
                clusters.enable_observability_on_acm_clusters()
                clusters.attach_clusters_to_acm_cluster_hub()

    finally:
        restore_gcp_configuration(gcp_params=gcp_params)
