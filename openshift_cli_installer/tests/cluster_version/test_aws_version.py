import click
import pytest

from openshift_cli_installer.tests.cluster_version.aws_base_versions import AWS_BASE_VERSIONS
from openshift_cli_installer.tests.cluster_version.constants import PARAMETRIZE_NEGATIVE_TESTS
from openshift_cli_installer.utils.cluster_versions import (
    get_cluster_version_to_install,
)


@pytest.mark.parametrize(
    "clusters",
    [
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
        ([{"version": "4.16", "stream": "ec", "expected": "4.16.0-ec.5"}]),
        ([{"version": "4.16.0-ec.5", "stream": "ec", "expected": "4.16.0-ec.5"}]),
        ([{"version": "4.15", "stream": "rc", "expected": "4.15.0-rc.8"}]),
        ([{"version": "4.15.0-rc.8", "stream": "rc", "expected": "4.15.0-rc.8"}]),
        ([{"version": "4.16", "stream": "ci", "expected": "4.16.0-0.ci-2024-04-17-034741"}]),
        ([{"version": "4.16.0-0.ci-2024-04-17-034741", "stream": "ci", "expected": "4.16.0-0.ci-2024-04-17-034741"}]),
        ([{"version": "4.15.8", "stream": "stable", "expected": "4.15.8"}]),
    ],
)
def test_aws_cluster_version(clusters):
    for cluster in clusters:
        res = get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=AWS_BASE_VERSIONS,
            platform="aws",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )

        assert res == cluster["expected"]


@pytest.mark.parametrize(
    "cluster",
    PARAMETRIZE_NEGATIVE_TESTS,
)
def test_aws_cluster_version_negative(cluster):
    with pytest.raises(click.Abort):
        get_cluster_version_to_install(
            wanted_version=cluster["version"],
            base_versions_dict=AWS_BASE_VERSIONS,
            platform="aws",
            stream=cluster["stream"],
            log_prefix="test-cluster-versions",
        )
