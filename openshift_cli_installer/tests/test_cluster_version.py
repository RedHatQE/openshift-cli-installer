import click
import pytest

from openshift_cli_installer.tests.all_osd_versions import BASE_AVAILABLE_OSD_VERSIONS_DICT
from openshift_cli_installer.tests.all_rosa_versions import BASE_AVAILABLE_ROSA_VERSIONS_DICT
from openshift_cli_installer.tests.utils import update_aws_clusters_versions
from openshift_cli_installer.utils.cluster_versions import update_rosa_osd_clusters_versions


@pytest.mark.parametrize(
    "clusters, expected",
    [
        (
            [{"version": "4.13", "stream": "stable", "platform": "aws"}],
            [
                {
                    "version": "4.13.33",
                    "stream": "stable",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
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
                    "version": "4.13.33",
                    "stream": "stable",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                },
                {
                    "version": "4.13.0-0.nightly-2024-02-09-115610",
                    "stream": "nightly",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                },
            ],
        ),
        (
            [{"version": "4.13", "stream": "nightly", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-0.nightly-2024-02-09-115610",
                    "stream": "nightly",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                }
            ],
        ),
        (
            [{"version": "4.16", "stream": "ec", "platform": "aws"}],
            [
                {
                    "version": "4.16.0-ec.2",
                    "stream": "ec",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                }
            ],
        ),
        (
            [{"version": "4.13", "stream": "rc", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-rc.8",
                    "stream": "rc",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                }
            ],
        ),
        (
            [{"version": "4.13", "stream": "ci", "platform": "aws"}],
            [
                {
                    "version": "4.13.0-0.ci-2024-02-10-025244",
                    "stream": "ci",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
                }
            ],
        ),
        (
            [{"version": "4.13.33", "stream": "stable", "platform": "aws"}],
            [
                {
                    "version": "4.13.33",
                    "stream": "stable",
                    "platform": "aws",
                    "version-url": "openshift-release.apps.ci.l2s4.p1.openshiftapps.com",
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
        "aws_4.16_ec",
        "aws_4.13_rc",
        "aws_4.13_ci",
        "aws_4.13.33_stable",
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
            [{"version": "4.13", "channel-group": "stable", "platform": "rosa"}],
            [{"version": "4.13.6", "channel-group": "stable", "platform": "rosa"}],
        ),
        (
            [{"version": "4.13", "channel-group": "nightly", "platform": "rosa"}],
            [{"version": "4.13.0-0.nightly-2023-08-15-023315", "channel-group": "nightly", "platform": "rosa"}],
        ),
        (
            [{"version": "4.13", "channel-group": "candidate", "platform": "rosa"}],
            [{"version": "4.13.9", "channel-group": "candidate", "platform": "rosa"}],
        ),
        (
            [{"version": "4.13.6", "channel-group": "stable", "platform": "rosa"}],
            [{"version": "4.13.6", "channel-group": "stable", "platform": "rosa"}],
        ),
        (
            [
                {"version": "4.13", "channel-group": "stable", "platform": "rosa"},
                {"version": "4.13", "channel-group": "nightly", "platform": "rosa"},
            ],
            [
                {"version": "4.13.6", "channel-group": "stable", "platform": "rosa"},
                {"version": "4.13.0-0.nightly-2023-08-15-023315", "channel-group": "nightly", "platform": "rosa"},
            ],
        ),
        ([{"version": "4", "channel-group": "stable", "platform": "rosa"}], "error"),
        ([{"version": "100.5.1", "channel-group": "stable", "platform": "rosa"}], "error"),
        ([{"version": "100.5", "channel-group": "stable", "platform": "rosa"}], "error"),
        ([{"version": "4.13.40", "channel-group": "stable", "platform": "rosa"}], "error"),
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
            clusters=clusters, _test=True, _test_versions_dict=BASE_AVAILABLE_ROSA_VERSIONS_DICT
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
            [{"version": "4.13", "channel-group": "stable", "platform": "aws-osd"}],
            [{"version": "4.13.9", "channel-group": "stable", "platform": "aws-osd"}],
        ),
        (
            [{"version": "4.13", "channel-group": "nightly", "platform": "aws-osd"}],
            [{"version": "4.13.0-0.nightly-2023-08-25-012257", "channel-group": "nightly", "platform": "aws-osd"}],
        ),
        (
            [{"version": "4.13", "channel-group": "candidate", "platform": "aws-osd"}],
            [{"version": "4.13.10", "channel-group": "candidate", "platform": "aws-osd"}],
        ),
        (
            [{"version": "4.13.9", "channel-group": "stable", "platform": "aws-osd"}],
            [{"version": "4.13.9", "channel-group": "stable", "platform": "aws-osd"}],
        ),
        (
            [
                {"version": "4.13", "channel-group": "stable", "platform": "aws-osd"},
                {"version": "4.13", "channel-group": "nightly", "platform": "aws-osd"},
            ],
            [
                {"version": "4.13.9", "channel-group": "stable", "platform": "aws-osd"},
                {"version": "4.13.0-0.nightly-2023-08-25-012257", "channel-group": "nightly", "platform": "aws-osd"},
            ],
        ),
        ([{"version": "4", "channel-group": "stable", "platform": "aws-osd"}], "error"),
        ([{"version": "100.5.1", "channel-group": "stable", "platform": "aws-osd"}], "error"),
        ([{"version": "100.5", "channel-group": "stable", "platform": "aws-osd"}], "error"),
        ([{"version": "4.13.40", "channel-group": "stable", "platform": "aws-osd"}], "error"),
        (
            [{"version": "4.13", "channel-group": "stable", "platform": "gcp-osd"}],
            [{"version": "4.13.9", "channel-group": "stable", "platform": "gcp-osd"}],
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
            clusters=clusters, _test=True, _test_versions_dict=BASE_AVAILABLE_OSD_VERSIONS_DICT
        )
        assert res == expected
    except click.Abort:
        if expected == "error":
            return
        else:
            raise
