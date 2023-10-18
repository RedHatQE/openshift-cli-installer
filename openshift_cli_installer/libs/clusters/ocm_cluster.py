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
        self.logger = get_logger(
            f"{self.__class__.__module__}-{self.__class__.__name__}"
        )

        self.expiration_time = None
        self.osd_base_available_versions_dict = {}
        self.rosa_base_available_versions_dict = {}
        self.channel_group = self.cluster.get("channel-group", "stable")
        self.multi_az = self.cluster.get("multi-az", False)
        self.ocm_env = self.cluster.get("ocm-env", STAGE_STR)

        self.prepare_cluster_data()
        self.cluster_object = Cluster(
            client=self.ocm_client,
            name=self.name,
        )
        self._set_expiration_time()
        self.dump_cluster_data_to_file()

    def _set_expiration_time(self):
        expiration_time = self.cluster.get("expiration-time")
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
