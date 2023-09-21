import click
import pytest

from openshift_cli_installer.libs.unmanaged_clusters.aws_ipi_clusters import (
    update_aws_clusters_versions,
)
from openshift_cli_installer.tests.all_osd_versions import (
    BASE_AVAILABLE_OSD_VERSIONS_DICT,
)
from openshift_cli_installer.tests.all_rosa_versions import (
    BASE_AVAILABLE_ROSA_VERSIONS_DICT,
)
from openshift_cli_installer.utils.clusters import update_rosa_osd_clusters_versions


@pytest.mark.parametrize(
    "clusters, expected",
    [
        (
            [{"version": "4.13", "stream": "stable", "platform": "aws"}],
            [
                {
                    "version": "4.13.9-x86_64",
                    "stream": "stable",
                    "platform": "aws",
                    "version_url": "quay.io/openshift-release-dev/ocp-release",
                }
            ],
        ),
        (
            [
                {"version": "4.13", "stream": "stable", "platform": "aws"},
                {"version": "4.13", "stream": "nightly", "platform": "aws"},
            ],
            [
                {
                    "version": "4.13.9-x86_64",
                    "stream": "stable",
                    "platform": "aws",
                    "version_url": "quay.io/openshift-release-dev/ocp-release",
                },
                {
                    "version": "4.13.0-0.nightly-2023-08-15-023315",
                    "stream": "nightly",
                    "platform": "aws",
                    "version_url": "registry.ci.openshift.org/ocp/release",
                },
            ],
        ),
        (
            [{"version": "4.13", "stream": "nightly", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-0.nightly-2023-08-15-023315",
                    "stream": "nightly",
                    "platform": "aws",
                    "version_url": "registry.ci.openshift.org/ocp/release",
                }
            ],
        ),
        (
            [{"version": "4.13", "stream": "ec", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-ec.4-x86_64",
                    "stream": "ec",
                    "platform": "aws",
                    "version_url": "quay.io/openshift-release-dev/ocp-release",
                }
            ],
        ),
        (
            [{"version": "4.13", "stream": "rc", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-rc.8-x86_64",
                    "stream": "rc",
                    "platform": "aws",
                    "version_url": "quay.io/openshift-release-dev/ocp-release",
                }
            ],
        ),
        (
            [{"version": "4.13", "stream": "ci", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-0.ci-2023-08-14-170508",
                    "stream": "ci",
                    "platform": "aws",
                    "version_url": "registry.ci.openshift.org/ocp/release",
                }
            ],
        ),
        (
            [{"version": "4.13.9", "stream": "stable", "platform": "aws"}],
            [
                {
                    "version": "4.13.9-x86_64",
                    "stream": "stable",
                    "platform": "aws",
                    "version_url": "quay.io/openshift-release-dev/ocp-release",
                }
            ],
        ),
        ([{"version": "4", "stream": "stable", "platform": "aws"}], "error"),
        ([{"version": "100.5.1", "stream": "stable", "platform": "aws"}], "error"),
        ([{"version": "100.5", "stream": "stable", "platform": "aws"}], "error"),
        ([{"version": "4.13.40", "stream": "stable", "platform": "aws"}], "error"),
    ],
    ids=[
        "aws_4.13_stable",
        "aws_4.13_stable_and_nightly",
        "aws_4.13_nightly",
        "aws_4.13_ec",
        "aws_4.13_rc",
        "aws_4.13_ci",
        "aws_4.13.9_stable",
        "aws_4_stable_negative",
        "aws_100.5.1_stable_negative",
        "aws_100.5_stable_negative",
        "aws_4.13.40_stable_negative",
    ],
)
def test_aws_cluster_version(clusters, expected):
    try:
        res = update_aws_clusters_versions(clusters=clusters, _test=True)
        assert res == expected
    except click.Abort:
        if expected == "error":
            return
        else:
            raise


@pytest.mark.parametrize(
    "clusters, expected",
    [
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            [
                {
                    "version": "4.13.6",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "nightly",
                    "platform": "rosa",
                }
            ],
            [
                {
                    "version": "4.13.0-0.nightly-2023-08-15-023315",
                    "channel-group": "nightly",
                    "platform": "rosa",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "candidate",
                    "platform": "rosa",
                }
            ],
            [
                {
                    "version": "4.13.9",
                    "channel-group": "candidate",
                    "platform": "rosa",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13.6",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            [
                {
                    "version": "4.13.6",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "stable",
                    "platform": "rosa",
                },
                {
                    "version": "4.13",
                    "channel-group": "nightly",
                    "platform": "rosa",
                },
            ],
            [
                {
                    "version": "4.13.6",
                    "channel-group": "stable",
                    "platform": "rosa",
                },
                {
                    "version": "4.13.0-0.nightly-2023-08-15-023315",
                    "channel-group": "nightly",
                    "platform": "rosa",
                },
            ],
        ),
        (
            [
                {
                    "version": "4",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "100.5.1",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "100.5",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "4.13.40",
                    "channel-group": "stable",
                    "platform": "rosa",
                }
            ],
            "error",
        ),
    ],
    ids=[
        "rosa_4.13_stable",
        "rosa_4.13_nightly",
        "rosa_4.13_candidate",
        "rosa_4.13.6_stable",
        "rosa_4.13_stable_and_nightly",
        "rosa_4_stable_negative",
        "rosa_100.5.1_stable_negative",
        "rosa_100.5_stable_negative",
        "rosa_4.13.40_stable_negative",
    ],
)
def test_rosa_cluster_version(clusters, expected):
    try:
        res = update_rosa_osd_clusters_versions(
            clusters=clusters,
            _test=True,
            _test_versions_dict=BASE_AVAILABLE_ROSA_VERSIONS_DICT,
        )
        assert res == expected
    except click.Abort:
        if expected == "error":
            return
        else:
            raise


@pytest.mark.parametrize(
    "clusters, expected",
    [
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            [
                {
                    "version": "4.13.9",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "nightly",
                    "platform": "aws-osd",
                }
            ],
            [
                {
                    "version": "4.13.0-0.nightly-2023-08-25-012257",
                    "channel-group": "nightly",
                    "platform": "aws-osd",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "candidate",
                    "platform": "aws-osd",
                }
            ],
            [
                {
                    "version": "4.13.10",
                    "channel-group": "candidate",
                    "platform": "aws-osd",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13.9",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            [
                {
                    "version": "4.13.9",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                },
                {
                    "version": "4.13",
                    "channel-group": "nightly",
                    "platform": "aws-osd",
                },
            ],
            [
                {
                    "version": "4.13.9",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                },
                {
                    "version": "4.13.0-0.nightly-2023-08-25-012257",
                    "channel-group": "nightly",
                    "platform": "aws-osd",
                },
            ],
        ),
        (
            [
                {
                    "version": "4",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "100.5.1",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "100.5",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "4.13.40",
                    "channel-group": "stable",
                    "platform": "aws-osd",
                }
            ],
            "error",
        ),
        (
            [
                {
                    "version": "4.13",
                    "channel-group": "stable",
                    "platform": "gcp-osd",
                }
            ],
            [
                {
                    "version": "4.13.9",
                    "channel-group": "stable",
                    "platform": "gcp-osd",
                }
            ],
        ),
    ],
    ids=[
        "aws_osd_4.13_stable",
        "aws_osd_4.13_nightly",
        "aws_osd_4.13_candidate",
        "aws_osd_4.13.6_stable",
        "aws_osd_4.13_stable_and_nightly",
        "aws_osd_4_stable_negative",
        "aws_osd_100.5.1_stable_negative",
        "aws_osd_100.5_stable_negative",
        "aws_osd_4.13.40_stable_negative",
        "gcp_osd_4.13_stable",
    ],
)
def test_osd_cluster_version(clusters, expected):
    try:
        res = update_rosa_osd_clusters_versions(
            clusters=clusters,
            _test=True,
            _test_versions_dict=BASE_AVAILABLE_OSD_VERSIONS_DICT,
        )
        assert res == expected
    except click.Abort:
        if expected == "error":
            return
        else:
            raise
