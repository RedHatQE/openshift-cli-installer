import json

import click
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.clusters.ocm_cluster import OcmCluster
from openshift_cli_installer.utils.cluster_versions import filter_versions
from openshift_cli_installer.utils.const import AWS_OSD_STR, GCP_OSD_STR
from openshift_cli_installer.utils.general import zip_and_upload_to_s3


class OsdCluster(OcmCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        if self.gcp_service_account_file:
            self.gcp_service_account = self.get_service_account_dict_from_file()

        if self.create:
            self.replicas = self.cluster.get("replicas")
            self.compute_machine_type = self.cluster.get("compute-machine-type")
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

        except Exception as ex:
            self.logger.error(
                f"{self.log_prefix}: Failed to run cluster create \n{ex}",
            )
            self.set_cluster_auth()

            if self.must_gather_output_dir:
                self.collect_must_gather()

            self.destroy_cluster()
            raise click.Abort()

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
