import functools
import re
from typing import Dict, List

import click
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


def get_cluster_version_to_install(
    wanted_version: str, base_versions_dict: Dict, platform: str, stream: str, log_prefix: str
) -> str:
    wanted_version_len = len(wanted_version.split("."))
    if wanted_version_len < 2:
        LOGGER.error(f"{log_prefix}: Version must be at least x.y (4.3), got {wanted_version}")
        raise click.Abort()

    match = None

    for _source, versions in base_versions_dict.items():
        if platform in (HYPERSHIFT_STR, ROSA_STR, AWS_OSD_STR, GCP_OSD_STR) and stream != _source:
            continue

        if wanted_version_len == 2:
            if _match := versions.get(wanted_version):
                if stream != "stable" and platform in IPI_BASED_PLATFORMS:
                    _match = [_ver for _ver in _match if stream in _ver]
                match = _match[0]
                break

        else:
            _version_key = re.findall(r"^\d+.\d+", wanted_version)[0]
            if _match := [_version for _version in versions.get(_version_key, []) if _version == wanted_version]:
                match = _match[0]
                break

    if not match:
        LOGGER.error(f"{log_prefix}: Cluster version {wanted_version} not found for stream {stream}")
        raise click.Abort()

    LOGGER.success(f"{log_prefix}: Cluster version set to {match} [{stream}]")
    return match


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


@functools.cache
def parse_openshift_release_url():
    url = "https://openshift-release.apps.ci.l2s4.p1.openshiftapps.com"
    LOGGER.info(f"Parsing {url}")
    req = requests.get(url)
    soup = BeautifulSoup(req.text, "html.parser")
    return soup.find_all("tr")
