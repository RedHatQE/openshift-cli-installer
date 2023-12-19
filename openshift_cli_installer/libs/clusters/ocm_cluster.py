from datetime import datetime, timedelta

import rosa.cli
from ocm_python_wrapper.cluster import Cluster
from ocm_python_wrapper.versions import Versions
from simple_logger.logger import get_logger

from openshift_cli_installer.libs.clusters.ocp_cluster import OCPCluster
from openshift_cli_installer.utils.const import HYPERSHIFT_STR, STAGE_STR
from openshift_cli_installer.utils.general import tts


class OcmCluster(OCPCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")

        destroy_from_s3_bucket_or_local_directory = kwargs.get("destroy_from_s3_bucket_or_local_directory")

        if not destroy_from_s3_bucket_or_local_directory:
            self.osd_base_available_versions_dict = {}
            self.rosa_base_available_versions_dict = {}
            self.cluster["channel-group"] = self.cluster_info["channel-group"] = self.cluster.get(
                "channel-group", "stable"
            )
            self.cluster["multi-az"] = self.cluster_info["multi-az"] = self.cluster.get("multi-az", False)
            self.cluster["ocm-env"] = self.cluster_info["ocm-env"] = self.cluster.get("ocm-env", STAGE_STR)

            self._set_expiration_time()
            self.dump_cluster_data_to_file()

        self.prepare_cluster_data()
        self.cluster_object = Cluster(
            client=self.ocm_client,
            name=self.cluster_info["name"],
        )

    def _set_expiration_time(self):
        expiration_time = self.cluster.get("expiration-time")
        if expiration_time:
            _expiration_time = tts(ts=expiration_time)
            self.cluster["expiration-time"] = self.cluster_info["expiration-time"] = (
                f"{(datetime.now() + timedelta(seconds=_expiration_time)).isoformat()}Z"
            )

    def get_osd_versions(self):
        self.osd_base_available_versions_dict.update(
            Versions(client=self.ocm_client).get(channel_group=self.cluster_info["channel-group"])
        )

    def get_rosa_versions(self):
        base_available_versions = rosa.cli.execute(
            command=(
                f"list versions --channel-group={self.cluster_info['channel-group']} "
                f"{'--hosted-cp' if self.cluster_info['platform'] == HYPERSHIFT_STR else ''}"
            ),
            aws_region=self.cluster_info["region"],
            ocm_client=self.ocm_client,
        )["out"]
        _all_versions = [ver["raw_id"] for ver in base_available_versions]
        self.rosa_base_available_versions_dict.setdefault(self.cluster_info["channel-group"], []).extend(_all_versions)
