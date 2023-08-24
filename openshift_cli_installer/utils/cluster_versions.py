import re

import click
import semver

from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    AWS_STR,
    HYPERSHIFT_STR,
    ROSA_STR,
)


def set_clusters_versions(clusters, base_available_versions):
    all_available_versions = {}
    for _cluster_data in clusters:
        stream = get_cluster_stream(cluster_data=_cluster_data)
        all_available_versions.update(
            filter_versions(
                version=_cluster_data["version"],
                base_versions_dict=base_available_versions,
                platform=_cluster_data["platform"],
                stream=stream,
            )
        )

    for cluster_data in clusters:
        stream = get_cluster_stream(cluster_data=cluster_data)
        cluster_version = cluster_data["version"]
        platform = cluster_data["platform"]
        version_key = get_split_version(version=cluster_version)
        all_stream_versions = all_available_versions[stream][version_key]
        err_msg = f"Cluster version {cluster_version} not found for stream {stream}"
        if len(cluster_version.split(".")) == 3:
            for _ver in all_stream_versions["versions"]:
                if cluster_version in _ver:
                    cluster_data["version"] = _ver
                    break
            else:
                click.secho(f"{err_msg}", fg="red")
                raise click.Abort()
        elif len(cluster_version.split(".")) < 2:
            click.secho(
                f"Version must be at least x.y (4.3), got {cluster_version}", fg="red"
            )
            raise click.Abort()
        else:
            try:
                cluster_data["version"] = all_stream_versions["latest"]
            except KeyError:
                click.secho(f"{err_msg}", fg="red")
                raise click.Abort()

        if platform == AWS_STR:
            version_url = [
                url
                for url, versions in base_available_versions.items()
                if cluster_data["version"] in versions
            ]
            if version_url:
                cluster_data["version_url"] = version_url[0]
            else:
                click.secho(
                    f"Cluster version url not found for {cluster_version} in"
                    f" {base_available_versions.keys()}",
                    fg="red",
                )
                raise click.Abort()

        click.echo(f"Cluster version set to {cluster_data['version']}")

    return clusters


def filter_versions(version, base_versions_dict, platform, stream):
    versions_dict = {}
    version_key = get_split_version(version=version)
    x86_64_str = "-x86_64"
    versions_dict[stream] = {version_key: {"versions": set(), "latest": ""}}

    for _source, versions in base_versions_dict.items():
        if platform in (HYPERSHIFT_STR, ROSA_STR, AWS_OSD_STR) and stream != _source:
            continue

        reg_stream = get_regex_str_for_version_match(
            platform=platform, stream=stream, x86_64_str=x86_64_str
        )

        match = re.findall(
            rf"({re.escape(version)}(.\d+)?(-)?(\d+.)?{reg_stream}.*)",
            "\n".join(versions),
        )
        if match:
            versions_dict[stream][version_key]["versions"] = set(
                [_match[0] for _match in match]
            )

        all_semver_versions = set()
        for available_version in versions_dict[stream][version_key].get("versions", []):
            stripped = False
            if x86_64_str in available_version:
                stripped = True
                available_version = available_version.replace(x86_64_str, "")
            try:
                all_semver_versions.add(
                    (semver.Version.parse(available_version), stripped)
                )
            except ValueError:
                continue

        if all_semver_versions:
            max_version = max([str(ver[0]) for ver in all_semver_versions])
            add_x86_64 = [
                ver[1] for ver in all_semver_versions if str(ver[0]) == max_version
            ][0]
            versions_dict[stream][version_key][
                "latest"
            ] = f"{max_version}{x86_64_str if add_x86_64 else ''}"

    if not versions_dict[stream][version_key]["versions"]:
        click.secho(
            f"Cluster version {version} not found for stream {stream}", fg="red"
        )
        raise click.Abort()
    return versions_dict


def get_split_version(version):
    split_version = version.split(".")
    if len(split_version) > 2:
        version = ".".join(split_version[:-1])

    return version


def get_regex_str_for_version_match(platform, stream, x86_64_str):
    reg_stream = ""
    if platform in (HYPERSHIFT_STR, ROSA_STR) and stream == "nightly":
        reg_stream = stream

    if platform == AWS_STR:
        reg_stream = stream
        if stream == "stable":
            reg_stream = x86_64_str.strip("-")

        elif stream not in ("nightly", "ci"):
            reg_stream = rf"{stream}.\d+{x86_64_str}"

    return reg_stream


def get_cluster_stream(cluster_data):
    _platform = cluster_data["platform"]
    return (
        cluster_data["stream"]
        if _platform == AWS_STR
        else cluster_data["channel-group"]
    )
