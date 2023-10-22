import os
import re
import shutil

import click
import rosa.cli
from python_terraform import IsNotFlagged, Terraform
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.clusters.ocm_cluster import OcmCluster
from openshift_cli_installer.utils.cluster_versions import filter_versions
from openshift_cli_installer.utils.const import HYPERSHIFT_STR
from openshift_cli_installer.utils.general import (
    get_manifests_path,
    zip_and_upload_to_s3,
)


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
            self.machine_cidr = self.cluster.get("cidr", "10.0.0.0/16")
            self.cidr = self.cluster.get("cidr")
            self.private_subnets = self.cluster.get("private_subnets")
            self.public_subnets = self.cluster.get("public_subnets")

        self.dump_cluster_data_to_file()

    def terraform_init(self):
        self.logger.info(f"{self.log_prefix}: Init Terraform")
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
        self.terraform_init()
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
        self.logger.info(f"{self.log_prefix}: Preparing hypershift VPCs")
        self.terraform_init()
        shutil.copy(
            os.path.join(get_manifests_path(), "setup-vpc.tf"), self.cluster_dir
        )
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
            "_already_processed",
            "must_gather_output_dir",
            "acm_observability_s3_region",
            "acm_observability_storage_type",
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

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster create\n{ex}",
            )
            self.set_cluster_auth()
            if self.must_gather_output_dir:
                self.collect_must_gather()

            self.destroy_cluster()
            raise click.Abort()

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
