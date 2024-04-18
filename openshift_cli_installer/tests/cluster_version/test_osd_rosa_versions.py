import click
import pytest

from openshift_cli_installer.tests.cluster_version.constants import PARAMETRIZE_NEGATIVE_TESTS
from openshift_cli_installer.tests.cluster_version.rosa_osd_base_versions import ROSA_OSD_BASE_VERSIONS
from openshift_cli_installer.utils.cluster_versions import (
    get_cluster_version_to_install,
)

PARAMETRIZE_TESTS = [
    ([{"version": "4.15", "stream": "stable", "expected": "4.15.8"}]),
    ([
        {"version": "4.15", "stream": "stable", "expected": "4.15.8"},
        {
            "version": "4.16",
            "stream": "nightly",
            "expected": "4.16.0-0.nightly-2024-04-16-195622",
        },
    ]),
    ([
        {
            "version": "4.16.0-0.nightly-2024-04-16-195622",
            "stream": "nightly",
            "expected": "4.16.0-0.nightly-2024-04-16-195622",
        }
    ]),
    ([{"version": "4.15.8", "stream": "stable", "expected": "4.15.8"}]),
    ([{"version": "4.15", "stream": "candidate", "expected": "4.15.9"}]),
]


@pytest.mark.parametrize(
    "clusters",
    PARAMETRIZE_TESTS,
)
def test_osd_cluster_version(clusters):
    for cluster in clusters:
        res = get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=ROSA_OSD_BASE_VERSIONS,
            platform="aws-osd",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )

        assert res == cluster["expected"]


@pytest.mark.parametrize(
    "cluster",
    PARAMETRIZE_NEGATIVE_TESTS,
)
def test_osd_cluster_version_negative(cluster):
    with pytest.raises(click.Abort):
        get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=ROSA_OSD_BASE_VERSIONS,
            platform="aws-osd",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )


@pytest.mark.parametrize(
    "clusters",
    PARAMETRIZE_TESTS,
)
def test_rosa_cluster_version(clusters):
    for cluster in clusters:
        res = get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=ROSA_OSD_BASE_VERSIONS,
            platform="rosa",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )

        assert res == cluster["expected"]


@pytest.mark.parametrize(
    "cluster",
    PARAMETRIZE_NEGATIVE_TESTS,
)
def test_rosa_cluster_version_negative(cluster):
    with pytest.raises(click.Abort):
        get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=ROSA_OSD_BASE_VERSIONS,
            platform="rosa",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )
