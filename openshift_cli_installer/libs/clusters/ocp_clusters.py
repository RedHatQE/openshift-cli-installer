import functools
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import rosa.cli
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from google.cloud import compute_v1
from google.oauth2 import service_account
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.clusters.aws_ipi_cluster import AwsIpiCluster
from openshift_cli_installer.libs.clusters.osd_cluster import OsdCluster
from openshift_cli_installer.libs.clusters.rosa_cluster import RosaCluster
from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    PRODUCTION_STR,
    ROSA_STR,
    STAGE_STR,
)


class OCPClusters(UserInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )
        self.aws_ipi_clusters = []
        self.aws_osd_clusters = []
        self.rosa_clusters = []
        self.hypershift_clusters = []
        self.gcp_osd_clusters = []

        self.s3_target_dirs = []

        for _cluster in self.clusters:
            self.add(ocp_cluster=_cluster, **kwargs)

        if self.create:
            self.check_ocm_managed_existing_clusters()
            self.is_region_support_hypershift()
            self.is_region_support_aws()
            self.is_region_support_gcp()

    def add(self, ocp_cluster, **kwargs):
        _cluster_platform = ocp_cluster["platform"]
        if _cluster_platform == AWS_STR:
            self.aws_ipi_clusters.append(
                AwsIpiCluster(ocp_cluster=ocp_cluster, **kwargs)
            )

        if _cluster_platform == AWS_OSD_STR:
            self.aws_osd_clusters.append(OsdCluster(ocp_cluster=ocp_cluster, **kwargs))

        if _cluster_platform == ROSA_STR:
            self.rosa_clusters.append(RosaCluster(ocp_cluster=ocp_cluster, **kwargs))

        if _cluster_platform == HYPERSHIFT_STR:
            self.hypershift_clusters.append(
                RosaCluster(ocp_cluster=ocp_cluster, **kwargs)
            )

        if _cluster_platform == GCP_OSD_STR:
            self.gcp_osd_clusters.append(OsdCluster(ocp_cluster=ocp_cluster, **kwargs))

    @property
    def list_clusters(self):
        return (
            self.aws_ipi_clusters
            + self.aws_osd_clusters
            + self.rosa_clusters
            + self.hypershift_clusters
            + self.gcp_osd_clusters
        )

    @property
    @functools.cache
    def aws_managed_clusters(self):
        return self.rosa_clusters + self.hypershift_clusters + self.aws_osd_clusters

    @property
    @functools.cache
    def ocm_managed_clusters(self):
        return self.aws_managed_clusters + self.gcp_osd_clusters

    def check_ocm_managed_existing_clusters(self):
        if self.ocm_managed_clusters:
            self.logger.info("Check for existing OCM-managed clusters.")
            existing_clusters_list = []
            for _cluster in self.ocm_managed_clusters:
                if _cluster.cluster_object.exists:
                    existing_clusters_list.append(_cluster.name)

            if existing_clusters_list:
                self.logger.error(
                    f"At least one cluster already exists: {existing_clusters_list}",
                )
                raise click.Abort()

    @staticmethod
    def _hypershift_regions(ocm_client):
        rosa_regions = rosa.cli.execute(
            command="list regions",
            aws_region="us-west-2",
            ocm_client=ocm_client,
        )["out"]
        return [
            region["id"]
            for region in rosa_regions
            if region["supports_hypershift"] is True
        ]

    def is_region_support_hypershift(self):
        if self.hypershift_clusters:
            self.logger.info(f"Check if regions are {HYPERSHIFT_STR}-supported.")
            unsupported_regions = []
            hypershift_regions_dict = {PRODUCTION_STR: None, STAGE_STR: None}
            for _cluster in self.hypershift_clusters:
                _hypershift_regions = hypershift_regions_dict[_cluster.ocm_env]
                if not _hypershift_regions:
                    _hypershift_regions = self._hypershift_regions(
                        ocm_client=_cluster.ocm_client
                    )
                    hypershift_regions_dict[_cluster.ocm_env] = _hypershift_regions

                if _cluster.region not in _hypershift_regions:
                    unsupported_regions.append(
                        f"Cluster {_cluster.name}, region: {_cluster.region}\n"
                    )

                if unsupported_regions:
                    self.logger.error(
                        f"The following {HYPERSHIFT_STR} clusters regions are no"
                        f" supported: {unsupported_regions}.\nSupported hypershift"
                        f" regions are: {_hypershift_regions}",
                    )
                    raise click.Abort()

    def is_region_support_aws(self):
        _clusters = self.aws_ipi_clusters + self.aws_managed_clusters
        if _clusters:
            self.logger.info(f"Check if regions are {AWS_STR}-supported.")
            _regions_to_verify = set()
            for _cluster in self.aws_ipi_clusters + self.aws_managed_clusters:
                _regions_to_verify.add(_cluster.region)

            for _region in _regions_to_verify:
                set_and_verify_aws_credentials(region_name=_region)

    def _get_gcp_regions(self):
        credentials = service_account.Credentials.from_service_account_file(
            self.gcp_service_account_file
        )
        return [
            region.name
            for region in compute_v1.RegionsClient(credentials=credentials)
            .list(project=credentials.project_id)
            .items
        ]

    def is_region_support_gcp(self):
        if self.gcp_osd_clusters:
            self.logger.info("Check if regions are GCP-supported.")
            supported_regions = self._get_gcp_regions()
            unsupported_regions = []
            for _cluster in self.gcp_osd_clusters:
                cluster_region = _cluster.region
                if cluster_region not in supported_regions:
                    unsupported_regions.append(
                        f"cluster: {_cluster.name}, region: {cluster_region}"
                    )

            if unsupported_regions:
                self.logger.error(
                    "The following clusters regions are not supported in GCP:"
                    f" {unsupported_regions}"
                )
                raise click.Abort()

    def run_create_or_destroy_clusters(self):
        futures = []
        action_str = "create_cluster" if self.create else "destroy_cluster"
        processed_clusters = []

        with ThreadPoolExecutor() as executor:
            for cluster in self.list_clusters:
                action_func = getattr(cluster, action_str)
                click.echo(
                    f"Executing {self.action} cluster {cluster.name} [parallel:"
                    f" {self.parallel}]"
                )
                if self.parallel:
                    futures.append(executor.submit(action_func))
                else:
                    processed_clusters.append(action_func())

        if futures:
            for result in as_completed(futures):
                if result.exception():
                    self.logger.error(
                        f"Failed to {self.action} cluster: {result.exception()}\n",
                    )
                    raise click.Abort()
                processed_clusters.append(result.result())

        return processed_clusters

    def delete_s3_target_dirs(self):
        for _dir in self.s3_target_dirs:
            shutil.rmtree(path=_dir, ignore_errors=True)
