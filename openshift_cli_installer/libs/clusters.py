import base64
import copy
import functools
import json
import os
import re
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import click
import rosa.cli
import shortuuid
import yaml
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from clouds.aws.session_clients import s3_client
from google.cloud import compute_v1
from google.oauth2 import service_account
from ocm_python_wrapper.cluster import Cluster
from ocm_python_wrapper.ocm_client import OCMPythonClient
from ocm_python_wrapper.versions import Versions
from ocp_resources.managed_cluster import ManagedCluster
from ocp_resources.multi_cluster_hub import MultiClusterHub
from ocp_resources.multi_cluster_observability import MultiClusterObservability
from ocp_resources.namespace import Namespace
from ocp_resources.route import Route
from ocp_resources.secret import Secret
from ocp_resources.utils import TimeoutWatch
from ocp_utilities.infra import get_client
from ocp_utilities.must_gather import run_must_gather
from ocp_utilities.utils import run_command
from python_terraform import IsNotFlagged, Terraform
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.unmanaged_clusters.aws_ipi_clusters import (
    generate_unified_pull_secret,
    get_aws_versions,
    get_install_config_j2_template,
    get_local_ssh_key,
)
from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.cli_utils import (
    change_home_environment_on_openshift_ci,
    get_cluster_data_by_name_from_clusters,
)
from openshift_cli_installer.utils.cluster_versions import (
    filter_versions,
    get_cluster_stream,
    get_split_version,
)
from openshift_cli_installer.utils.clusters import get_kubeadmin_token
from openshift_cli_installer.utils.const import (
    AWS_BASED_PLATFORMS,
    AWS_OSD_STR,
    AWS_STR,
    CLUSTER_DATA_YAML_FILENAME,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    PRODUCTION_STR,
    ROSA_STR,
    S3_STR,
    STAGE_STR,
    TIMEOUT_60MIN,
)
from openshift_cli_installer.utils.general import (
    get_manifests_path,
    tts,
    zip_and_upload_to_s3,
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

        for _cluster in self.clusters:
            _cluster_platform = _cluster["platform"]
            if _cluster_platform == AWS_STR:
                self.aws_ipi_clusters.append(
                    AwsIpiCluster(ocp_cluster=_cluster, **kwargs)
                )

            if _cluster_platform == AWS_OSD_STR:
                self.aws_osd_clusters.append(OsdCluster(ocp_cluster=_cluster, **kwargs))

            if _cluster_platform == ROSA_STR:
                self.rosa_clusters.append(RosaCluster(ocp_cluster=_cluster, **kwargs))

            if _cluster_platform == HYPERSHIFT_STR:
                self.hypershift_clusters.append(
                    RosaCluster(ocp_cluster=_cluster, **kwargs)
                )

            if _cluster_platform == GCP_OSD_STR:
                self.gcp_osd_clusters.append(OsdCluster(ocp_cluster=_cluster, **kwargs))

        if self.create:
            self.check_ocm_managed_existing_clusters()
            self.is_region_support_hypershift()
            self.is_region_support_aws()
            self.is_region_support_gcp()

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


class OCPCluster(UserInput):
    def __init__(self, ocp_cluster, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )
        self.ocp_cluster = ocp_cluster
        self.name = self.ocp_cluster["name"]
        self.shortuuid = shortuuid.uuid()
        self.platform = self.ocp_cluster["platform"]
        self.log_prefix = f"[Cluster - {self.name} | Platform - {self.platform}]"
        self.timeout = tts(ts=self.ocp_cluster.get("timeout", TIMEOUT_60MIN))

        self.ocm_env = None
        self.ocm_client = None
        self.s3_object_name = None
        self.install_version = None
        self.version_url = None
        self.ssh_key = None
        self.pull_secret = None
        self.base_domain = None
        self.kubeadmin_token = None
        self.timeout_watch = None
        self.all_available_versions = {}

        self.region = self.ocp_cluster["region"]
        self.acm = self.ocp_cluster.get("acm") is True
        self.acm_observability = self.ocp_cluster.get("acm-observability") is True
        self.acm_observability_storage_type = self.ocp_cluster.get(
            "acm-observability-storage-type"
        )
        self.acm_observability_s3_region = self.ocp_cluster.get(
            "acm-observability-s3-region", self.region
        )
        self.acm_clusters = self.ocp_cluster.get("acm-clusters")
        self.version = self.ocp_cluster["version"]
        self.stream = get_cluster_stream(cluster_data=self.ocp_cluster)
        self.cluster_dir = os.path.join(
            self.clusters_install_data_directory, self.platform, self.name
        )
        self.auth_path = os.path.join(self.cluster_dir, "auth")
        self.kubeconfig_path = os.path.join(self.auth_path, "kubeconfig")

        Path(self.auth_path).mkdir(parents=True, exist_ok=True)
        self._add_s3_bucket_data()

        self.dump_cluster_data_to_file()

    @property
    def to_dict(self):
        return self.__dict__

    def start_time_watcher(self):
        if self.timeout_watch:
            self.logger.info(
                f"{self.log_prefix}: Reusing timeout watcher, time left: "
                f"{timedelta(seconds=self.timeout_watch.remaining_time())}"
            )
            return self.timeout_watch

        self.logger.info(
            f"{self.log_prefix}: Start timeout watcher, time left:"
            f" {timedelta(seconds=self.timeout)}"
        )
        return TimeoutWatch(timeout=self.timeout)

    def prepare_cluster_data(self):
        supported_envs = (PRODUCTION_STR, STAGE_STR)
        if self.ocm_env not in supported_envs:
            self.logger.error(
                f"{self.log_prefix}: got unsupported OCM env - {self.ocm_env},"
                f" supported envs: {supported_envs}"
            )
            raise click.Abort()

        self.ocm_client = self.get_ocm_client()

    def get_ocm_client(self):
        return OCMPythonClient(
            token=self.ocm_token,
            endpoint="https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
            api_host=self.ocm_env,
            discard_unknown_keys=True,
        ).client

    def _add_s3_bucket_data(self):
        self.s3_object_name = (
            f"{f'{self.s3_bucket_path}/' if self.s3_bucket_path else ''}{self.name}-{self.shortuuid}.zip"
        )

    def set_cluster_install_version(self):
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

        self.logger.success(
            f"{self.log_prefix}: Cluster version set to {self.install_version}"
        )

    def dump_cluster_data_to_file(self):
        _cluster_data = copy.copy(self.to_dict)
        _cluster_data.pop("ocm_client", "")
        _cluster_data.pop("timeout_watch", "")
        _cluster_data.pop("ocp_client", "")
        _cluster_data.pop("cluster_object", "")
        _cluster_data.pop("logger", "")
        _cluster_data.pop("clusters", "")
        with open(
            os.path.join(self.cluster_dir, CLUSTER_DATA_YAML_FILENAME), "w"
        ) as fd:
            fd.write(yaml.dump(_cluster_data))

    def collect_must_gather(self):
        try:
            target_dir = os.path.join(
                self.must_gather_output_dir, "must-gather", self.platform, self.name
            )
        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to get data; must-gather could not be"
                f" executed on: {ex}"
            )
            return

        try:
            if not os.path.exists(self.kubeconfig_path):
                self.logger.error(
                    f"{self.log_prefix}: kubeconfig does not exist; cannot run"
                    " must-gather."
                )
                return

            self.logger.info(
                f"{self.log_prefix}: Prepare must-gather target extracted directory"
                f" {target_dir}."
            )
            Path(target_dir).mkdir(parents=True, exist_ok=True)

            click.echo(
                f"Collect must-gather for cluster {self.name} running on"
                f" {self.platform}"
            )
            run_must_gather(
                target_base_dir=target_dir,
                kubeconfig=self.kubeconfig_path,
            )
            self.logger.success(f"{self.log_prefix}: must-gather collected")

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to run must-gather \n{ex}",
            )

            self.logger.info(
                f"{self.log_prefix}: Delete must-gather target directory {target_dir}."
            )
            shutil.rmtree(target_dir)

    def add_cluster_info_to_cluster_object(self):
        """
        Adds cluster information to the given clusters data dictionary.

        `cluster-id`, `api-url` and `console-url` (when available) will be added to `cluster_data`.
        """
        if self.cluster_object:
            self.ocp_client = self.cluster_object.ocp_client
            self.cluster_id = self.cluster_object.cluster_id

        else:
            self.ocp_client = get_client(config_file=self.kubeconfig_path)

        self.api_url = self.ocp_client.configuration.host
        console_route = Route(
            name="console", namespace="openshift-console", client=self.ocp_client
        )
        if console_route.exists:
            route_spec = console_route.instance.spec
            self.console_url = f"{route_spec.port.targetPort}://{route_spec.host}"

        self.dump_cluster_data_to_file()

    def set_cluster_auth(self):
        Path(self.auth_path).mkdir(parents=True, exist_ok=True)

        with open(os.path.join(self.auth_path, "kubeconfig"), "w") as fd:
            fd.write(yaml.dump(self.cluster_object.kubeconfig))

        with open(os.path.join(self.auth_path, "kubeadmin-password"), "w") as fd:
            fd.write(self.cluster_object.kubeadmin_password)

        self.dump_cluster_data_to_file()

    def delete_cluster_s3_buckets(self):
        self.logger.info(f"{self.log_prefix}: Deleting S3 bucket")
        buckets_to_delete = []
        _s3_client = s3_client()
        for _bucket in _s3_client.list_buckets()["Buckets"]:
            if _bucket["Name"].startswith(self.name):
                buckets_to_delete.append(_bucket["Name"])

        for _bucket in buckets_to_delete:
            self.logger.info(f"{self.log_prefix}: Deleting S3 bucket {_bucket}")
            for _object in _s3_client.list_objects(Bucket=_bucket)["Contents"]:
                _s3_client.delete_object(Bucket=_bucket, Key=_object["Key"])

            _s3_client.delete_bucket(Bucket=_bucket)

    def save_kubeadmin_token_to_clusters_install_data(self):
        # Do not run this function in parallel, get_kubeadmin_token() do `oc login`.
        with change_home_environment_on_openshift_ci():
            with get_kubeadmin_token(
                cluster_dir=self.cluster_dir, api_url=self.api_url
            ) as kubeadmin_token:
                self.kubeadmin_token = kubeadmin_token

        self.dump_cluster_data_to_file()

    def install_acm(self):
        self.logger.info(f"{self.log_prefix}: Installing ACM")
        run_command(
            command=shlex.split(f"cm install acm --kubeconfig {self.kubeconfig_path}"),
        )
        cluster_hub = MultiClusterHub(
            client=self.ocp_client,
            name="multiclusterhub",
            namespace="open-cluster-management",
        )
        cluster_hub.wait_for_status(
            status=cluster_hub.Status.RUNNING,
            timeout=self.timeout_watch.remaining_time(),
        )

        self.logger.success(f"{self.log_prefix}: ACM installed successfully")

        if self.acm_observability:
            self.enable_observability()

    def enable_observability(self):
        thanos_secret_data = None
        _s3_client = None

        bucket_name = f"{self.name}-observability-{self.shortuuid}"

        if self.acm_observability_storage_type == S3_STR:
            _s3_client = s3_client(region_name=self.acm_observability_s3_region)
            s3_secret_data = f"""
            type: {S3_STR}
            config:
              bucket: {bucket_name}
              endpoint: s3.{self.acm_observability_s3_region}.amazonaws.com
              insecure: true
              access_key: {self.aws_access_key_id}
              secret_key: {self.aws_secret_access_key}
            """
            s3_secret_data_bytes = s3_secret_data.encode("ascii")
            thanos_secret_data = {
                "thanos.yaml": base64.b64encode(s3_secret_data_bytes).decode("utf-8")
            }
            self.logger.info(
                f"{self.log_prefix}: Create S3 bucket {bucket_name} in"
                f" {self.acm_observability_s3_region}"
            )
            _s3_client.create_bucket(
                Bucket=bucket_name.lower(),
                CreateBucketConfiguration={
                    "LocationConstraint": self.acm_observability_s3_region
                },
            )

        try:
            open_cluster_management_observability_ns = Namespace(
                client=self.ocp_client, name="open-cluster-management-observability"
            )
            open_cluster_management_observability_ns.deploy(wait=True)
            openshift_pull_secret = Secret(
                client=self.ocp_client, name="pull-secret", namespace="openshift-config"
            )
            observability_pull_secret = Secret(
                client=self.ocp_client,
                name="multiclusterhub-operator-pull-secret",
                namespace=open_cluster_management_observability_ns.name,
                data_dict={
                    ".dockerconfigjson": openshift_pull_secret.instance.data[
                        ".dockerconfigjson"
                    ]
                },
                type="kubernetes.io/dockerconfigjson",
            )
            observability_pull_secret.deploy(wait=True)
            thanos_secret = Secret(
                client=self.ocp_client,
                name="thanos-object-storage",
                namespace=open_cluster_management_observability_ns.name,
                type="Opaque",
                data_dict=thanos_secret_data,
            )
            thanos_secret.deploy(wait=True)

            multi_cluster_observability_data = {
                "name": thanos_secret.name,
                "key": "thanos.yaml",
            }
            multi_cluster_observability = MultiClusterObservability(
                client=self.ocp_client,
                name="observability",
                metric_object_storage=multi_cluster_observability_data,
            )
            multi_cluster_observability.deploy(wait=True)
            multi_cluster_observability.wait_for_condition(
                condition=multi_cluster_observability.Condition.READY,
                status=multi_cluster_observability.Condition.Status.TRUE,
                timeout=self.timeout_watch.remaining_time(),
            )
            self.logger.success(f"{self.log_prefix}: Observability enabled")
        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to enable observability. error: {ex}"
            )

            if self.platform in AWS_BASED_PLATFORMS:
                for _bucket in _s3_client.list_buckets()["Buckets"]:
                    if _bucket["Name"] == bucket_name:
                        _s3_client.delete_bucket(Bucket=bucket_name)

            raise click.Abort()

    def attach_clusters_to_acm_hub(self):
        futures = []
        processed_clusters = []
        with ThreadPoolExecutor() as executor:
            for _managed_acm_cluster in self.acm_clusters:
                _managed_acm_cluster_data = get_cluster_data_by_name_from_clusters(
                    name=_managed_acm_cluster, clusters=self.acm_clusters
                )
                _managed_cluster_name = _managed_acm_cluster_data["name"]
                _managed_cluster_platform = _managed_acm_cluster_data["platform"]
                managed_acm_cluster_kubeconfig = (
                    self.get_cluster_kubeconfig_from_install_dir(
                        cluster_name=_managed_cluster_name,
                        cluster_platform=_managed_cluster_platform,
                    )
                )
                action_kwargs = {
                    "managed_acm_cluster_name": _managed_cluster_name,
                    "acm_cluster_kubeconfig": self.kubeconfig_path,
                    "managed_acm_cluster_kubeconfig": managed_acm_cluster_kubeconfig,
                }

                self.logger.info(
                    f"{self.log_prefix}: Attach {_managed_cluster_name} to ACM hub"
                )

                if self.parallel:
                    futures.append(
                        executor.submit(self.attach_cluster_to_acm, **action_kwargs)
                    )
                else:
                    processed_clusters.append(
                        self.attach_cluster_to_acm(**action_kwargs)
                    )

        if futures:
            for result in as_completed(futures):
                _exception = result.exception()
                if _exception:
                    self.logger.error(
                        f"{self.log_prefix}: Failed to attach"
                        f" {_managed_cluster_name} to ACM hub Error: {_exception}"
                    )
                    raise click.Abort()

    def attach_cluster_to_acm(
        self,
        managed_acm_cluster_name,
        acm_cluster_kubeconfig,
        managed_acm_cluster_kubeconfig,
    ):
        self.logger.info(
            f"{self.log_prefix}: Attach {managed_acm_cluster_name} to ACM hub"
        )

        run_command(
            command=shlex.split(
                f"cm --kubeconfig {acm_cluster_kubeconfig} attach cluster --cluster"
                f" {managed_acm_cluster_name} --cluster-kubeconfig"
                f" {managed_acm_cluster_kubeconfig}  --wait"
            ),
            check=False,
            verify_stderr=False,
        )

        managed_cluster = ManagedCluster(
            client=self.ocm_client, name=managed_acm_cluster_name
        )
        managed_cluster.wait_for_condition(
            condition="ManagedClusterImportSucceeded",
            status=managed_cluster.Condition.Status.TRUE,
            timeout=self.timeout_watch.remaining_time(),
        )
        self.logger.success(
            f"{self.log_prefix}: successfully attached {managed_acm_cluster_name} to"
            " ACM hub"
        )

    def get_cluster_kubeconfig_from_install_dir(self, cluster_name, cluster_platform):
        cluster_install_dir = os.path.join(
            self.clusters_install_data_directory, cluster_platform, cluster_name
        )
        if not os.path.exists(cluster_install_dir):
            self.logger.error(
                f"{self.log_prefix}: ACM managed cluster data dir not found in"
                f" {cluster_install_dir}"
            )
            raise click.Abort()

        return os.path.join(cluster_install_dir, "auth", "kubeconfig")


class AwsIpiCluster(OCPCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        self.openshift_install_binary_path = None
        self.aws_base_available_versions = None
        self.ocp_client = None
        self.cluster_id = None
        self.api_url = None
        self.console_url = None
        self.ocm_env = PRODUCTION_STR

        self.prepare_cluster_data()
        self._prepare_aws_ipi_cluster()
        self.dump_cluster_data_to_file()

    def _prepare_aws_ipi_cluster(self):
        self.base_domain = self.ocp_cluster["base_domain"]
        self.aws_base_available_versions = get_aws_versions()
        self.all_available_versions.update(
            filter_versions(
                wanted_version=self.version,
                base_versions_dict=self.aws_base_available_versions,
                platform=self.platform,
                stream=self.stream,
            )
        )
        self.set_cluster_install_version()
        self._set_install_version_url()
        self._aws_download_installer()
        if self.create:
            self._create_install_config_file()

    def _aws_download_installer(self):
        openshift_install_str = "openshift-install"
        binary_dir = os.path.join("/tmp", self.version_url)
        self.openshift_install_binary_path = os.path.join(
            binary_dir, openshift_install_str
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
                f"{self.log_prefix}: Failed to get {openshift_install_str} for version"
                f" {self.version_url}, error: {err}",
            )
            raise click.Abort()

    def _create_install_config_file(self):
        self.pull_secret = generate_unified_pull_secret(
            registry_config_file=self.registry_config_file,
            docker_config_file=self.docker_config_file,
        )
        self.ssh_key = get_local_ssh_key(ssh_key_file=self.ssh_key_file)
        cluster_install_config = get_install_config_j2_template(
            cluster_dict=self.to_dict
        )

        with open(os.path.join(self.cluster_dir, "install-config.yaml"), "w") as fd:
            fd.write(yaml.dump(cluster_install_config))

    def _set_install_version_url(self):
        version_url = [
            url
            for url, versions in self.aws_base_available_versions.items()
            if self.install_version in versions
        ]
        if version_url:
            self.version_url = f"{version_url[0]}:{self.install_version}"
        else:
            self.logger.error(
                f"{self.log_prefix}: Cluster version url not found for"
                f" {self.version} in {self.aws_base_available_versions.keys()}",
            )
            raise click.Abort()

    def run_installer_command(self, raise_on_failure):
        res, out, err = run_command(
            command=shlex.split(
                f"{self.openshift_install_binary_path} {self.action} cluster --dir"
                f" {self.cluster_dir}"
            ),
            capture_output=False,
            check=False,
        )

        if not res:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster {self.action} \n\tERR:"
                f" {err}\n\tOUT: {out}.",
            )
            if raise_on_failure:
                raise click.Abort()

        return res, out, err

    def create_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        res, _, _ = self.run_installer_command(raise_on_failure=False)

        if res:
            self.add_cluster_info_to_cluster_object()
            self.logger.success(f"{self.log_prefix}: Cluster created successfully")
            self.save_kubeadmin_token_to_clusters_install_data()
            if self.acm:
                self.install_acm()
                if self.acm_observability:
                    self.enable_observability()

                if self.acm_clusters:
                    self.attach_clusters_to_acm_hub()

        if self.s3_bucket_name:
            zip_and_upload_to_s3(
                install_dir=self.cluster_dir,
                s3_bucket_name=self.s3_bucket_name,
                s3_bucket_path=self.s3_bucket_path,
                uuid=self.shortuuid,
            )

        if not res:
            if self.must_gather_output_dir:
                self.collect_must_gather()

            self.logger.warning(f"{self.log_prefix}: Cleaning cluster leftovers.")
            self.destroy_cluster()

            raise click.Abort()

    def destroy_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        self.run_installer_command(raise_on_failure=True)
        self.logger.success(f"{self.log_prefix}: Cluster destroyed")
        self.delete_cluster_s3_buckets()


class OcmCluster(OCPCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        self.expiration_time = None
        self.osd_base_available_versions_dict = {}
        self.rosa_base_available_versions_dict = {}
        self.channel_group = self.ocp_cluster.get("channel-group", "stable")
        self.multi_az = self.ocp_cluster.get("multi-az", False)
        self.ocm_env = self.ocp_cluster.get("ocm-env", STAGE_STR)

        self.prepare_cluster_data()
        self.cluster_object = Cluster(
            client=self.ocm_client,
            name=self.name,
        )
        self._set_expiration_time()
        self.dump_cluster_data_to_file()

    def _set_expiration_time(self):
        expiration_time = self.ocp_cluster.get("expiration-time")
        if expiration_time:
            _expiration_time = tts(ts=expiration_time)
            self.expiration_time = (
                f"{(datetime.now() + timedelta(seconds=_expiration_time)).isoformat()}Z"
            )

    def get_osd_versions(self):
        self.osd_base_available_versions_dict.update(
            Versions(client=self.ocm_client).get(channel_group=self.channel_group)
        )

    def get_rosa_versions(self):
        base_available_versions = rosa.cli.execute(
            command=(
                f"list versions --channel-group={self.channel_group} "
                f"{'--hosted-cp' if self.platform == HYPERSHIFT_STR else ''}"
            ),
            aws_region=self.region,
            ocm_client=self.ocm_client,
        )["out"]
        _all_versions = [ver["raw_id"] for ver in base_available_versions]
        self.rosa_base_available_versions_dict.setdefault(
            self.channel_group, []
        ).extend(_all_versions)


class OsdCluster(OcmCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        self.gcp_service_account = self.get_service_account_dict_from_file()

        if self.create:
            self.replicas = self.ocp_cluster.get("replicas")
            self.compute_machine_type = self.ocp_cluster.get("compute-machine-type")
            self.get_osd_versions()
            self.all_available_versions.update(
                filter_versions(
                    wanted_version=self.version,
                    base_versions_dict=self.osd_base_available_versions_dict,
                    platform=self.platform,
                    stream=self.stream,
                )
            )

            self.set_cluster_install_version()

        self.dump_cluster_data_to_file()

    def get_service_account_dict_from_file(self):
        with open(self.gcp_service_account_file) as fd:
            return json.loads(fd.read())

    def create_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        try:
            ocp_version = (
                self.install_version
                if self.channel_group != "candidate"
                else f"{self.install_version}-candidate"
            )
            provision_osd_kwargs = {
                "wait_for_ready": True,
                "wait_timeout": self.timeout_watch.remaining_time(),
                "region": self.region,
                "ocp_version": ocp_version,
                "replicas": self.replicas,
                "compute_machine_type": self.compute_machine_type,
                "multi_az": self.multi_az,
                "channel_group": self.channel_group,
                "expiration_time": self.expiration_time,
                "platform": self.platform.replace("-osd", ""),
            }
            if self.platform == AWS_OSD_STR:
                provision_osd_kwargs.update(
                    {
                        "aws_access_key_id": self.aws_access_key_id,
                        "aws_account_id": self.aws_account_id,
                        "aws_secret_access_key": self.aws_secret_access_key,
                    }
                )
            elif self.platform == GCP_OSD_STR:
                provision_osd_kwargs.update(
                    {"gcp_service_account": self.gcp_service_account}
                )

            self.cluster_object.provision_osd(**provision_osd_kwargs)

            self.add_cluster_info_to_cluster_object()
            self.set_cluster_auth()

            self.logger.success(f"{self.log_prefix}: Cluster created successfully")
            self.save_kubeadmin_token_to_clusters_install_data()
            if self.acm:
                self.install_acm()
                if self.acm_observability:
                    self.enable_observability()

                if self.acm_clusters:
                    self.attach_clusters_to_acm_hub()

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster create \n{ex}",
            )
            self.set_cluster_auth()

            if self.must_gather_output_dir:
                self.collect_must_gather()

            self.destroy_cluster()
            raise click.Abort()

        finally:
            if self.s3_bucket_name:
                zip_and_upload_to_s3(
                    install_dir=self.cluster_dir,
                    s3_bucket_name=self.s3_bucket_name,
                    s3_bucket_path=self.s3_bucket_path,
                    uuid=self.shortuuid,
                )

    def destroy_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        try:
            self.cluster_object.delete(timeout=self.timeout_watch.remaining_time())
            self.logger.success(f"{self.log_prefix}: Cluster destroyed successfully")
            self.delete_cluster_s3_buckets()
        except Exception as ex:
            self.logger.error(f"{self.log_prefix}: Failed to run cluster delete\n{ex}")
            raise click.Abort()


class RosaCluster(OcmCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        if self.create:
            self.get_rosa_versions()
            self.all_available_versions.update(
                filter_versions(
                    wanted_version=self.version,
                    base_versions_dict=self.rosa_base_available_versions_dict,
                    platform=self.platform,
                    stream=self.stream,
                )
            )
            self.set_cluster_install_version()

        if self.platform == HYPERSHIFT_STR:
            self.oidc_config_id = None
            self.terraform = None
            self.subnet_ids = None
            self.hosted_cp = "true"
            self.tags = "dns:external"
            self.machine_cidr = self.ocp_cluster.get("cidr", "10.0.0.0/16")
            self.cidr = self.ocp_cluster.get("cidr")
            self.private_subnets = self.ocp_cluster.get("private_subnets")
            self.public_subnets = self.ocp_cluster.get("public_subnets")
            self.terraform_init()

        self.dump_cluster_data_to_file()

    def terraform_init(self):
        # az_id example: us-east-2 -> ["use2-az1", "use2-az2"]
        az_id_prefix = "".join(re.match(r"(.*)-(\w).*-(\d)", self.region).groups())
        cluster_parameters = {
            "aws_region": self.region,
            "az_ids": [f"{az_id_prefix}-az1", f"{az_id_prefix}-az2"],
            "cluster_name": self.name,
        }
        if self.cidr:
            cluster_parameters["cidr"] = self.cidr
        if self.private_subnets:
            cluster_parameters["private_subnets"] = self.private_subnets
        if self.public_subnets:
            cluster_parameters["public_subnets"] = self.public_subnets

        self.terraform = Terraform(
            working_dir=self.cluster_dir, variables=cluster_parameters
        )
        self.terraform.init()

    def create_oidc(self):
        self.logger.info(f"{self.log_prefix}: Create OIDC config")
        res = rosa.cli.execute(
            command=f"create oidc-config --managed=false --prefix={self.name}",
            aws_region=self.region,
            ocm_client=self.ocm_client,
        )
        oidc_id = re.search(r'"id": "([a-z0-9]+)",', res["out"])
        if not oidc_id:
            self.logger.error(f"{self.log_prefix}: Failed to get OIDC config")
            raise click.Abort()

        self.oidc_config_id = oidc_id.group(1)

    def delete_oidc(self):
        self.logger.info(f"{self.log_prefix}: Delete OIDC config")
        if not self.oidc_config_id:
            self.logger.warning(f"{self.log_prefix}: No OIDC config ID to delete")
            return

        rosa.cli.execute(
            command=f"delete oidc-config --oidc-config-id={self.oidc_config_id}",
            aws_region=self.region,
            ocm_client=self.ocm_client,
        )

    def destroy_hypershift_vpc(self):
        self.logger.info(f"{self.log_prefix}: Destroy hypershift VPCs")
        rc, _, err = self.terraform.destroy(
            force=IsNotFlagged,
            auto_approve=True,
            capture_output=True,
        )
        if rc != 0:
            self.logger.error(
                f"{self.log_prefix}: Failed to destroy hypershift VPCs with error:"
                f" {err}"
            )
            raise click.Abort()

    def prepare_hypershift_vpc(self):
        shutil.copy(
            os.path.join(get_manifests_path(), "setup-vpc.tf"), self.cluster_dir
        )
        self.logger.info(f"{self.log_prefix}: Preparing hypershift VPCs")
        self.terraform.plan(dir_or_plan="hypershift.plan")
        rc, _, err = self.terraform.apply(
            capture_output=True, skip_plan=True, auto_approve=True
        )
        if rc != 0:
            self.logger.error(
                f"{self.log_prefix}: Create hypershift VPC failed with"
                f" error: {err}, rolling back.",
            )
            self.delete_oidc()
            # Clean up already created resources from the plan
            self.destroy_hypershift_vpc()
            raise click.Abort()

        terraform_output = self.terraform.output()
        private_subnet = terraform_output["cluster-private-subnet"]["value"]
        public_subnet = terraform_output["cluster-public-subnet"]["value"]
        self.subnet_ids = f'"{public_subnet},{private_subnet}"'

    def build_rosa_command(self):
        hosted_cp_arg = "--hosted-cp"
        ignore_keys = (
            "name",
            "platform",
            "ocm_env",
            "ocm_token",
            "cluster_dir",
            "timeout",
            "auth_dir",
            "cidr",
            "private_subnets",
            "public_subnets",
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_account_id",
            "multi_az",
            "ocm_client",
            "shortuuid",
            "s3_object_name",
            "s3_bucket_name",
            "s3_bucket_path",
            "acm",
            "acm_clusters",
            "timeout_watch",
            "cluster_object",
            "acm_observability",
            "logger",
            "log_prefix",
            "gcp_service_account_file",
            "clusters_install_data_directory",
            "auth_path",
            "clusters_yaml_config_file",
            "version",
            "ssh_key_file",
            "registry_config_file",
            "action",
            "kubeconfig_path",
            "stream",
            "docker_config_file",
            "region",
        )
        ignore_prefix = ("acm-observability",)
        command = f"create cluster --sts --cluster-name={self.name} "
        command_kwargs = []
        for _key, _val in self.to_dict.items():
            if (
                _key in ignore_keys
                or _key.startswith(ignore_prefix)
                or not isinstance(_val, str)
            ):
                continue

            if _key == "install_version":
                _key = "version"

            command_kwargs.append(f"--{_key.replace('_', '-')}={_val}")

        for cmd in command_kwargs:
            if hosted_cp_arg in cmd:
                command += f"{hosted_cp_arg} "
            else:
                command += f"{cmd} "

        return command

    def create_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        if self.platform == HYPERSHIFT_STR:
            self.create_oidc()
            self.prepare_hypershift_vpc()

        self.dump_cluster_data_to_file()

        try:
            rosa.cli.execute(
                command=self.build_rosa_command(),
                ocm_client=self.ocm_client,
                aws_region=self.region,
            )

            self.cluster_object.wait_for_cluster_ready(
                wait_timeout=self.timeout_watch.remaining_time()
            )
            self.set_cluster_auth()
            self.add_cluster_info_to_cluster_object()
            self.logger.success(f"{self.log_prefix}: Cluster created successfully")
            self.save_kubeadmin_token_to_clusters_install_data()
            if self.acm:
                self.install_acm()
                if self.acm_observability:
                    self.enable_observability()

                if self.acm_clusters:
                    self.attach_clusters_to_acm_hub()

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster create\n{ex}",
            )
            try:
                self.set_cluster_auth()
                if self.must_gather_output_dir:
                    self.collect_must_gather()
            except Exception as ex:
                self.logger.error(
                    f"{self.log_prefix}: Failed to collect must gather\n{ex}",
                )

            self.destroy_cluster()
            raise click.Abort()

        finally:
            if self.s3_bucket_name:
                zip_and_upload_to_s3(
                    uuid=self.shortuuid,
                    install_dir=self.cluster_dir,
                    s3_bucket_name=self.s3_bucket_name,
                    s3_bucket_path=self.s3_bucket_path,
                )

    def destroy_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        should_raise = False
        try:
            res = rosa.cli.execute(
                command=f"delete cluster --cluster={self.name}",
                ocm_client=self.ocm_client,
                aws_region=self.region,
            )
            self.cluster_object.wait_for_cluster_deletion(
                wait_timeout=self.timeout_watch.remaining_time()
            )
            self.remove_leftovers(res=res)

        except Exception as ex:
            should_raise = ex

        if self.platform == HYPERSHIFT_STR:
            self.destroy_hypershift_vpc()
            self.delete_oidc()

        if should_raise:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster destroy\n{should_raise}"
            )
            raise click.Abort()

        self.logger.success(f"{self.log_prefix}: Cluster destroyed successfully")
        self.delete_cluster_s3_buckets()

    def remove_leftovers(self, res):
        leftovers = re.search(
            r"INFO: Once the cluster is uninstalled use the following commands to"
            r" remove"
            r" the above "
            r"aws resources(.*?)INFO:",
            res.get("out", ""),
            re.DOTALL,
        )
        if leftovers:
            for line in leftovers.group(1).splitlines():
                _line = line.strip()
                if _line.startswith("rosa"):
                    base_command = _line.split(maxsplit=1)[-1]
                    command = base_command.replace("-c ", "--cluster=")
                    command = command.replace("--prefix ", "--prefix=")
                    command = command.replace("--oidc-config-id ", "--oidc-config-id=")
                    rosa.cli.execute(
                        command=command,
                        ocm_client=self.ocm_client,
                        aws_region=self.region,
                    )
