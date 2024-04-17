import click
import pytest

from openshift_cli_installer.tests.cluster_version.rosa_base_versions import ROSA_BASE_VERSIONS
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
        ([{"version": "4.15.9", "stream": "stable", "expected": "4.15.9"}]),
        ([{"version": "4", "stream": "stable", "expected": "error"}]),
        ([{"version": "100.5.1", "stream": "stable", "expected": "error"}]),
        ([{"version": "100.5", "stream": "stable", "expected": "error"}]),
        ([{"version": "4.15.40", "stream": "stable", "expected": "error"}]),
    ],
)
def test_aws_cluster_version(clusters):
    for cluster in clusters:
        try:
            res = get_cluster_version_to_install(
                wanted_version=cluster["version"],
                base_versions_dict=ROSA_BASE_VERSIONS,
                platform="rosa",
                stream=cluster["stream"],
                log_prefix="test-cluster-versions",
                cluster_name="test-cluster",
            )

            assert res == cluster["expected"]
        except Exception as exp:
            if isinstance(exp, AssertionError):
                pass

            assert isinstance(exp, click.Abort)
