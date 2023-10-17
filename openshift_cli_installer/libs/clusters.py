import functools
import os
import shlex
from pathlib import Path

import click
import shortuuid
import yaml
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from ocm_python_wrapper.cluster import Cluster
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocp_utilities.utils import run_command
from simple_logger.logger import get_logger
import rosa.cli
from google.cloud import compute_v1
from google.oauth2 import service_account

from openshift_cli_installer.libs.unmanaged_clusters.aws_ipi_clusters import (
    get_all_versions,
    get_aws_versions,
    generate_unified_pull_secret,
    get_local_ssh_key,
    get_install_config_j2_template,
)
from openshift_cli_installer.utils.cluster_versions import (
    get_cluster_stream,
    filter_versions,
    get_split_version,
)
from openshift_cli_installer.utils.const import (
    PRODUCTION_STR,
    TIMEOUT_60MIN,
    AWS_STR,
    STAGE_STR,
    OCM_MANAGED_PLATFORMS,
    ROSA_STR,
    HYPERSHIFT_STR,
    AWS_OSD_STR,
    GCP_OSD_STR,
)
from openshift_cli_installer.utils.general import tts


class OCPClusters:
    def __init__(self, user_input):
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )
        self.user_input = user_input
        self.gcp_service_account_file = self.user_input.gcp_service_account_file
        self.list = []
        for _cluster in self.user_input.clusters:
            self.list.append(OCPCluster(cluster=_cluster, user_input=user_input))

    @functools.cache
    def _get_clusters_by_platform(self, platform):
        return [_cluster for _cluster in self.list if _cluster.platform == platform]

    @property
    @functools.cache
    def aws_ipi_clusters(self):
        return self._get_clusters_by_platform(platform=AWS_STR)

    @property
    @functools.cache
    def rosa_clusters(self):
        return self._get_clusters_by_platform(platform=ROSA_STR)

    @property
    @functools.cache
    def hypershift_clusters(self):
        return self._get_clusters_by_platform(platform=HYPERSHIFT_STR)

    @property
    @functools.cache
    def aws_osd_clusters(self):
        return self._get_clusters_by_platform(platform=AWS_OSD_STR)

    @property
    @functools.cache
    def gcp_osd_clusters(self):
        return self._get_clusters_by_platform(platform=GCP_OSD_STR)

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
                        f"The following {HYPERSHIFT_STR} clusters regions are no supported:"
                        f" {unsupported_regions}.\nSupported hypershift regions are:"
                        f" {_hypershift_regions}",
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


class OCPCluster:
    def __init__(self, cluster, user_input):
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )
        self.user_input = user_input
        self.cluster = cluster
        self.create = user_input.create
        self.name = self.cluster["name"]
        self.shortuuid = shortuuid.uuid()
        self.platform = self.cluster["platform"]
        self.log_prefix = f"[Cluster - {self.name} Platform - {self.platform}]"
        self.timeout = tts(ts=self.cluster.get("timeout", TIMEOUT_60MIN))
        self.ocm_token = self.user_input.ocm_token

        self.ocm_env = None
        self.ocm_client = None
        self.s3_object_name = None
        self.aws_base_available_versions = None
        self.install_version = None
        self.version_url = None
        self.registry_config_file = None
        self.docker_config_file = None
        self.ssh_key_file = None
        self.ssh_key = None
        self.pull_secret = None
        self.all_available_versions = {}

        self.version = self.cluster["version"]
        self.stream = get_cluster_stream(cluster_data=self.cluster)
        self.s3_bucket_name = user_input.s3_bucket_name
        self.s3_bucket_path = user_input.s3_bucket_path
        self.region = self.cluster["region"]
        self.clusters_install_data_directory = (
            self.user_input.clusters_install_data_directory
        )
        self.cluster_dir = os.path.join(
            self.clusters_install_data_directory, self.platform, self.name
        )
        self.auth_path = os.path.join(self.cluster_dir, "auth")

        Path(self.auth_path).mkdir(parents=True, exist_ok=True)
        self._prepare_cluster_data()
        self._add_s3_bucket_data()
        self._prepare_aws_ipi_clusters()

    def _prepare_cluster_data(self):
        supported_envs = (PRODUCTION_STR, STAGE_STR)
        if self.platform == AWS_STR:
            self.ocm_env = PRODUCTION_STR
        else:
            self.ocm_env = self.cluster.get("ocm-env", STAGE_STR)

        if self.ocm_env not in supported_envs:
            self.logger.error(
                f"{self.log_prefix}: got unsupported OCM env - {self.ocm_env}, supported"
                f" envs: {supported_envs}"
            )
            raise click.Abort()

        self.ocm_client = self.get_ocm_client()
        if self.platform in OCM_MANAGED_PLATFORMS:
            self.cluster_object = Cluster(
                client=self.ocm_client,
                name=self.name,
            )

    def get_ocm_client(self):
        return OCMPythonClient(
            token=self.ocm_token,
            endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
            api_host=self.ocm_env,
            discard_unknown_keys=True,
        ).client

    def _add_s3_bucket_data(self):
        self.s3_object_name = f"{f'{self.s3_bucket_path}/' if self.s3_bucket_path else ''}{self.name}-{self.shortuuid}.zip"

    def _prepare_aws_ipi_clusters(self):
        if self.platform == AWS_STR:
            self.ssh_key_file = self.user_input.ssh_key_file
            self.docker_config_file = self.user_input.docker_config_file
            self.registry_config_file = self.user_input.registry_config_file
            self.aws_base_available_versions = get_aws_versions()
            self.all_available_versions.update(
                filter_versions(
                    wanted_version=self.version,
                    base_versions_dict=self.aws_base_available_versions,
                    platform=self.platform,
                    stream=self.stream,
                )
            )
            self._set_cluster_install_version()
            self._aws_download_installer()
            self._create_install_config_file()

    def _aws_download_installer(self):
        openshift_install_str = "openshift-install"
        binary_dir = os.path.join("/tmp", self.version_url)
        self.openshift_install_binary_path = os.path.join(
            os.path.join("/tmp", self.version_url), openshift_install_str
        )
        rc, _, err = run_command(
            command=shlex.split(
                "oc adm release extract "
                f"{self.version_url} "
                f"--command={openshift_install_str} --to={binary_dir} --registry-config={self.registry_config_file}"
            ),
            check=False,
        )
        if not rc:
            self.logger.error(
                f"{self.log_prefix}: Failed to get {openshift_install_str} for version {self.version_url},"
                f" error: {err}",
            )
            raise click.Abort()

    def _create_install_config_file(self):
        self.pull_secret = generate_unified_pull_secret(
            registry_config_file=self.registry_config_file,
            docker_config_file=self.docker_config_file,
        )
        self.ssh_key = get_local_ssh_key(ssh_key_file=self.ssh_key_file)
        cluster_install_config = get_install_config_j2_template(
            cluster_dict=self.cluster
        )

        with open(os.path.join(self.cluster_dir, "install-config.yaml"), "w") as fd:
            fd.write(yaml.dump(cluster_install_config))

    def _set_cluster_install_version(self):
        version_key = get_split_version(version=self.version)
        all_stream_versions = self.all_available_versions[self.stream][version_key]
        err_msg = (
            f"{self.log_prefix}: Cluster version {self.version} not found for stream"
            f" {self.stream}"
        )
        if len(self.version.split(".")) == 3:
            for _ver in all_stream_versions["versions"]:
                if self.version in _ver:
                    self.install_version = _ver
                    break
            else:
                self.logger.error(f"{err_msg}")
                raise click.Abort()
        elif len(self.version.split(".")) < 2:
            self.logger.error(
                f"{self.log_prefix}: Version must be at least x.y (4.3), got"
                f" {self.version}",
            )
            raise click.Abort()
        else:
            try:
                self.install_version = all_stream_versions["latest"]
            except KeyError:
                self.logger.error(f"{err_msg}")
                raise click.Abort()

        if self.platform == AWS_STR:
            version_url = [
                url
                for url, versions in self.aws_base_available_versions.items()
                if self.version in versions
            ]
            import ipdb;ipdb.set_trace()
            if version_url:
                self.version_url = f"{version_url[0]}:{self.install_version}"
            else:
                self.logger.error(
                    f"{self.log_prefix}: Cluster version url not found for"
                    f" {self.version} in {self.aws_base_available_versions.keys()}",
                )
                raise click.Abort()

        self.logger.success(
            f"{self.log_prefix}: Cluster version set to {self.install_version}"
        )
