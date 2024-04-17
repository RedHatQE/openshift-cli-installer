import functools
import re
from typing import Dict, List

import click
import rosa.cli
from ocm_python_wrapper.versions import Versions
from simple_logger.logger import get_logger
import requests
from bs4 import BeautifulSoup

from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    GCP_OSD_STR,
    HYPERSHIFT_STR,
    ROSA_STR,
    IPI_BASED_PLATFORMS,
)


LOGGER = get_logger(name=__name__)


def set_clusters_versions(clusters, base_available_versions):
    for cluster_data in clusters:
        cluster_name = cluster_data.get("name", "test-cluster")
        cluster_version = cluster_data["version"]
        platform = cluster_data["platform"]

        if platform in IPI_BASED_PLATFORMS:
            version_url = [
                url for url, versions in base_available_versions.items() if cluster_data["version"] in versions
            ]
            if version_url:
                cluster_data["version-url"] = version_url[0]
            else:
                LOGGER.error(
                    f"{cluster_name}: Cluster version url not found for"
                    f" {cluster_version} in {base_available_versions.keys()}",
                )
                raise click.Abort()

        LOGGER.info(f"{cluster_name}: Cluster version set to {cluster_data['version']}")

    return clusters


def get_cluster_version_to_install(
    wanted_version: str, base_versions_dict: Dict, platform: str, stream: str, log_prefix: str, cluster_name: str
) -> str:
    wanted_version_len = len(wanted_version.split("."))
    if wanted_version_len < 2:
        LOGGER.error(f"{cluster_name}: Version must be at least x.y (4.3), got {wanted_version}")
        raise click.Abort()

    match = None

    for _source, versions in base_versions_dict.items():
        if platform in (HYPERSHIFT_STR, ROSA_STR, AWS_OSD_STR, GCP_OSD_STR) and stream != _source:
            continue

        if wanted_version_len == 2:
            if _match := versions.get(wanted_version):
                if stream != "stable":
                    _match = [_ver for _ver in _match if stream in _ver]
                match = _match[0]
                continue

        else:
            _version_key = re.findall(r"^\d+.\d+", wanted_version)[0]
            if _match := [_version for _version in versions.get(_version_key, []) if _version == wanted_version]:
                match = _match[0]
                continue

    if not match:
        LOGGER.error(f"Cluster version {wanted_version} not found for stream {stream}")
        raise click.Abort()

    LOGGER.success(f"{log_prefix}: Cluster version set to {match} [{stream}]")
    return match


def get_split_version(version):
    split_version = version.split(".")
    if len(split_version) > 2:
        version = ".".join(split_version[:-1])

    return version


def get_cluster_stream(cluster_data):
    _platform = cluster_data["platform"]
    return cluster_data["stream"] if _platform in IPI_BASED_PLATFORMS else cluster_data["channel-group"]


@functools.cache
def get_ipi_cluster_versions() -> Dict[str, Dict[str, List[str]]]:
    _source = "openshift-release.apps.ci.l2s4.p1.openshiftapps.com"
    _accepted_version_dict: Dict[str, Dict[str, List[str]]] = {_source: {}}
    for tr in parse_openshift_release_url():
        version, status = [_tr for _tr in tr.text.splitlines() if _tr][:2]
        if status == "Accepted":
            _version_key = re.findall(r"^\d+.\d+", version)[0]
            _accepted_version_dict[_source].setdefault(_version_key, []).append(version)

    return _accepted_version_dict


def update_rosa_osd_clusters_versions(clusters):
    base_available_versions_dict = {}
    for cluster_data in clusters:
        if cluster_data["platform"] in (AWS_OSD_STR, GCP_OSD_STR):
            base_available_versions_dict.update(
                Versions(client=cluster_data["ocm-client"]).get(channel_group=cluster_data["channel-group"])
            )

        elif cluster_data["platform"] in (ROSA_STR, HYPERSHIFT_STR):
            channel_group = cluster_data["channel-group"]
            base_available_versions = rosa.cli.execute(
                command=(
                    f"list versions --channel-group={channel_group} "
                    f"{'--hosted-cp' if cluster_data['platform'] == HYPERSHIFT_STR else ''}"
                ),
                aws_region=cluster_data["region"],
                ocm_client=cluster_data["ocm-client"],
            )["out"]
            _all_versions = [ver["raw_id"] for ver in base_available_versions]
            base_available_versions_dict.setdefault(channel_group, []).extend(_all_versions)

    return set_clusters_versions(clusters=clusters, base_available_versions=base_available_versions_dict)


@functools.cache
def parse_openshift_release_url():
    url = "https://openshift-release.apps.ci.l2s4.p1.openshiftapps.com"
    LOGGER.info(f"Parsing {url}")
    req = requests.get(url)
    soup = BeautifulSoup(req.text, "html.parser")
    return soup.find_all("tr")
