import functools
import re

import click
import rosa.cli
import semver
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
    all_available_versions = {}
    for _cluster_data in clusters:
        stream = get_cluster_stream(cluster_data=_cluster_data)
        all_available_versions.update(
            filter_versions(
                wanted_version=_cluster_data["version"],
                base_versions_dict=base_available_versions,
                platform=_cluster_data["platform"],
                stream=stream,
            )
        )

    for cluster_data in clusters:
        cluster_name = cluster_data.get("name", "test-cluster")
        stream = get_cluster_stream(cluster_data=cluster_data)
        cluster_version = cluster_data["version"]
        platform = cluster_data["platform"]
        version_key = get_split_version(version=cluster_version)
        all_stream_versions = all_available_versions[stream][version_key]
        err_msg = f"{cluster_name}: Cluster version {cluster_version} not found for stream {stream}"
        if len(cluster_version.split(".")) == 3:
            for _ver in all_stream_versions["versions"]:
                if cluster_version in _ver:
                    cluster_data["version"] = _ver
                    break
            else:
                LOGGER.error(err_msg)
                raise click.Abort()
        elif len(cluster_version.split(".")) < 2:
            LOGGER.error(f"{cluster_name}: Version must be at least x.y (4.3), got {cluster_version}")
            raise click.Abort()
        else:
            try:
                cluster_data["version"] = all_stream_versions["latest"]
            except KeyError:
                LOGGER.error(err_msg)
                raise click.Abort()

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


def filter_versions(wanted_version, base_versions_dict, platform, stream):
    versions_dict = {}
    version_key = get_split_version(version=wanted_version)
    versions_dict[stream] = {version_key: {"versions": set(), "latest": ""}}

    for _source, versions in base_versions_dict.items():
        if platform in (HYPERSHIFT_STR, ROSA_STR, AWS_OSD_STR, GCP_OSD_STR) and stream != _source:
            continue

        if stream == "stable" or stream == "candidate":
            if len(wanted_version.split(".")) == 3:
                match = re.findall(rf"({re.escape(wanted_version)})", "\n".join(versions))
                if match:
                    versions_dict[stream][version_key]["versions"].add(match[0])
            else:
                match = re.findall(rf"({re.escape(wanted_version)}(.\d+))", "\n".join(versions))

        else:
            match = re.findall(rf"({re.escape(wanted_version)}(.\d+)(-)?(\d+.)?({stream}).*)", "\n".join(versions))

        if match and not versions_dict[stream].get(version_key, {}).get("versions", []):
            versions_dict[stream][version_key]["versions"] = set([_match[0] for _match in match])

        all_semver_versions = set()
        for available_version in versions_dict[stream][version_key].get("versions", []):
            try:
                all_semver_versions.add((semver.Version.parse(available_version)))
            except ValueError:
                continue

        if all_semver_versions:
            max_version = str(max([ver for ver in all_semver_versions]))
            versions_dict[stream][version_key]["latest"] = f"{max_version}"

    if not versions_dict[stream][version_key]["versions"]:
        LOGGER.error(f"Cluster version {wanted_version} not found for stream {stream}")
        raise click.Abort()

    return versions_dict


def get_split_version(version):
    split_version = version.split(".")
    if len(split_version) > 2:
        version = ".".join(split_version[:-1])

    return version


def get_cluster_stream(cluster_data):
    _platform = cluster_data["platform"]
    return cluster_data["stream"] if _platform in IPI_BASED_PLATFORMS else cluster_data["channel-group"]


@functools.cache
def get_ipi_cluster_versions():
    _source = "openshift-release.apps.ci.l2s4.p1.openshiftapps.com"
    _accepted_version_dict = {_source: []}
    for tr in parse_openshift_release_url():
        version, status = [_tr for _tr in tr.text.splitlines() if _tr][:2]
        if status == "Accepted":
            _accepted_version_dict[_source].append(version)

    return _accepted_version_dict


def update_rosa_osd_clusters_versions(clusters, _test=False, _test_versions_dict=None):
    if _test:
        base_available_versions_dict = _test_versions_dict
    else:
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
