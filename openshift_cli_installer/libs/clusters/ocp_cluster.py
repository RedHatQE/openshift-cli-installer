import base64
import os
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import click
import shortuuid
import yaml
from clouds.aws.session_clients import s3_client
from ocm_python_wrapper.ocm_client import OCMPythonClient
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
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.user_input import UserInput
from openshift_cli_installer.utils.cli_utils import (
    change_home_environment_on_openshift_ci,
)
from openshift_cli_installer.utils.cluster_versions import (
    get_cluster_stream,
    get_split_version,
)
from openshift_cli_installer.utils.clusters import get_kubeadmin_token
from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    PRODUCTION_STR,
    S3_STR,
    STAGE_STR,
    TIMEOUT_60MIN,
)
from openshift_cli_installer.utils.general import tts


class OCPCluster(UserInput):
    def __init__(self, ocp_cluster, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )
        self.cluster = ocp_cluster
        self.name = self.cluster["name"]
        self.shortuuid = shortuuid.uuid()
        self.platform = self.cluster["platform"]
        self.region = self.cluster["region"]
        self.log_prefix = f"[C:{self.name}|P:{self.platform}|R:{self.region}]"
        self.timeout = tts(ts=self.cluster.get("timeout", TIMEOUT_60MIN))

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
        self.cluster_object = None
        self.cluster_id = None
        self.console_url = None
        self.api_url = None
        self.ocp_client = None
        self.all_available_versions = {}

        self.acm = self.cluster.get("acm") is True
        self.acm_clusters = self.cluster.get("acm-clusters")
        self.acm_observability = self.cluster.get("acm-observability") is True
        self.acm_observability_storage_type = self.cluster.get(
            "acm-observability-storage-type"
        )
        self.acm_observability_s3_region = self.cluster.get(
            "acm-observability-s3-region", self.region
        )

        self.version = self.cluster["version"]
        self.stream = get_cluster_stream(cluster_data=self.cluster)
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
        _cluster_data = {}
        keys_to_pop = (
            "ocm_client",
            "ocp_client",
            "cluster_object",
            "logger",
            "clusters",
            "user_kwargs",
            "log_prefix",
            "create",
            "action",
            "clusters_yaml_config_file",
            "all_available_versions",
            "gcp_service_account",
            "osd_base_available_versions_dict",
            "rosa_base_available_versions_dict",
            "replicas",
            "version",
            "timeout",
            "platform",
            "region",
            "stream",
            "name",
            "ocm_env",
            "terraform",
            "timeout_watch",
            "aws_base_available_versions",
            "base_domain",
            "channel_group",
            "acm_observability",
            "acm_observability_s3_region",
            "acm_observability_storage_type",
            "expiration_time",
            "compute_machine_type",
            "acm_clusters",
            "acm",
            "public_subnets",
            "private_subnets",
            "tags",
            "machine_cidr",
            "cidr",
            "hosted_cp",
            "_already_processed",
        )
        for _key, _val in self.to_dict.items():
            if _key in keys_to_pop or not _val:
                continue

            _cluster_data[_key] = _val

        _cluster_data_yaml_file = os.path.join(
            self.cluster_dir, CLUSTER_DATA_YAML_FILENAME
        )
        self.logger.info(
            f"{self.log_prefix}: Writing cluster data to {_cluster_data_yaml_file}"
        )
        with open(_cluster_data_yaml_file, "w") as fd:
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
            for _object in _s3_client.list_objects(Bucket=_bucket).get("Contents", []):
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

            if self.acm_observability_storage_type == S3_STR:
                for _bucket in _s3_client.list_buckets()["Buckets"]:
                    if _bucket["Name"] == bucket_name:
                        _s3_client.delete_bucket(Bucket=bucket_name)

            raise click.Abort()

    def attach_clusters_to_acm_hub(self, clusters):
        futures = []
        with ThreadPoolExecutor() as executor:
            for _managed_acm_cluster in self.acm_clusters:
                _managed_acm_cluster_object = clusters.get_cluster_object_by_name(
                    name=_managed_acm_cluster
                )
                _managed_cluster_name = _managed_acm_cluster_object.name
                managed_acm_cluster_kubeconfig = (
                    self.get_cluster_kubeconfig_from_install_dir(
                        cluster_name=_managed_cluster_name,
                        cluster_platform=_managed_acm_cluster_object.platform,
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
                    self.attach_cluster_to_acm(**action_kwargs)

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
        if not self.ocp_client:
            self.ocp_client = get_client(config_file=acm_cluster_kubeconfig)

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
            client=self.ocp_client, name=managed_acm_cluster_name
        )
        managed_cluster.wait_for_condition(
            condition="ManagedClusterImportSucceeded",
            status=managed_cluster.Condition.Status.TRUE,
            timeout=self.timeout_watch.remaining_time(),
        )
        self.logger.success(
            f"{self.log_prefix}: attached {managed_acm_cluster_name} to cluster"
            f" {self.name}"
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
