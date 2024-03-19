import shlex
import pytest
from ocp_utilities.utils import run_command
from openshift_cli_installer.utils.const import AWS_STR, AWS_OSD_STR, HYPERSHIFT_STR, GCP_STR, S3_STR
import subprocess


@pytest.mark.parametrize(
    "command, expected",
    [
        ("--ocm-token=123", "'action' must be provided, supported actions: `('destroy', 'create')`"),
        (
            f"--action=create --ocm-token='' --cluster='name=test-cl;platform={AWS_STR}'",
            "--ocm-token is required for clusters",
        ),
        ("--action=create --ocm-token=123", "At least one '--cluster' option must be provided"),
        (
            f"--action=create --ocm-token=123 --cluster='platform={AWS_STR}'",
            "Cluster name or name_prefix must be provided",
        ),
        (
            f"--action=create --ocm-token=123 --cluster='name=test-cl;platform={AWS_STR}' --cluster='name=test-cl;platform={AWS_STR}'",
            "Cluster names must be unique:",
        ),
        (
            f"--action=create --ocm-token=123 --registry-config-file=reg.json --cluster='name=test-cl;platform={AWS_STR};acm=true'",
            "The following keys must be booleans: ['acm']",
        ),
        (
            f"--action=create --ocm-token=123 --cluster='name=test-cl;platform={AWS_STR};log_level=unsupported'",
            "log levels are not supported for openshift-installer cli",
        ),
        (
            f"--action=create --ocm-token=123 --cluster='name=test-cl;platform={AWS_STR}'",
            "Registry config file is required for IPI cluster installations",
        ),
        (
            f"--action=create --ocm-token=123 --docker-config-file='' --registry-config-file=reg.json --cluster='name=test-cl;platform={AWS_STR}'",
            "Docker config file is required for IPI installations",
        ),
        (
            f"--action=create --ocm-token=123 --docker-config-file='dok.json' --registry-config-file=reg.json --ssh-key-file='' --cluster='name=test-cl;platform={AWS_STR}'",
            "SSH file is required for IPI cluster installations",
        ),
        (
            f"--action=create --ocm-token=123 --aws-secret-access-key='' --aws-access-key-id='' --cluster='name=test-cl;platform={AWS_OSD_STR}'",
            "--aws-secret-access-key and --aws-access-key-id required for AWS OSD OR ACM cluster installations",
        ),
        (
            f"--action=create --ocm-token=123 --aws-secret-access-key=123 --aws-access-key-id=123 --aws-account-id='' --cluster='name=test-cl;platform={AWS_OSD_STR}'",
            "--aws-account-id required for AWS OSD or Hypershift installations",
        ),
        (
            f"--action=create --ocm-token=123 --aws-secret-access-key=123 --aws-access-key-id=123 --aws-account-id=123 --cluster='name=test-cl;platform={HYPERSHIFT_STR};acm=True'",
            f"ACM not supported for {HYPERSHIFT_STR} clusters",
        ),
        (
            f"--action=create --ocm-token=123 --cluster='name=test-cl;platform={AWS_STR};acm-clusters=mycluser1'",
            "Managed ACM clusters: Cluster not found",
        ),
        (
            f"--action=create --ocm-token=123 --registry-config-file=reg.json --cluster='name=test-cl;platform={GCP_STR}'",
            "`--gcp-service-account-file` option must be provided for gcp-osd and gcp clusters",
        ),
        (
            f"--action=create --ocm-token=123 --registry-config-file=reg.json --cluster='name=test-cl;platform={AWS_STR};acm-observability=True;acm-observability-storage-type=bad'",
            "The following storage types are not supported for observability",
        ),
        (
            f"--action=create --ocm-token=123 --registry-config-file=reg.json --aws-secret-access-key='' --aws-access-key-id='' --cluster='name=test-cl;platform={AWS_STR};acm-observability=True;acm-observability-storage-type={S3_STR}'",
            "The following clusters are missing storage data for observability:",
        ),
        ('--action=create --ocm-token=123 --cluster="name=test-cl"', "is missing platform"),
        (
            "--action=create --ocm-token=123 --cluster='name=test-cl;platform=unsupported'",
            "platform 'unsupported' is not supported",
        ),
        (
            "--action=create --ocm-token=123 --destroy-clusters-from-s3-bucket --cluster='name=test-cl;platform=unsupported'",
            "`--s3-bucket-name` must be provided when running with",
        ),
        (
            "--action=create --ocm-token=123 --destroy-clusters-from-s3-bucket-query --cluster='name=test-cl;platform=unsupported'",
            "`--s3-bucket-name` must be provided when running with",
        ),
        (
            "--action=create --ocm-token=123 --destroy-clusters-from-install-data-directory --destroy-clusters-from-install-data-directory-using-s3-bucket --cluster='name=test-cl;platform=unsupported'",
            "`--destroy-clusters-from-install-data-directory-using-s3-bucket` is not supported when running with `--destroy-clusters-from-install-data-directory`",
        ),
    ],
)
def test_user_input(command, expected):
    base_command = "poetry run python openshift_cli_installer/cli.py --dry-run"

    if command:
        base_command += f" {command}"

    rc, _, err = run_command(
        command=shlex.split(base_command),
        verify_stderr=False,
        check=False,
        capture_output=False,
        stderr=subprocess.PIPE,
    )

    assert not rc
    assert expected in err
