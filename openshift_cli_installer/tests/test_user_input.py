import re
import pytest
from openshift_cli_installer.libs.user_input import UserInput, UserInputError
from openshift_cli_installer.utils.const import AWS_STR, AWS_OSD_STR, HYPERSHIFT_STR, GCP_STR, S3_STR

TEST_CL = {"name": "test-cl", "platform": AWS_STR}
CLUSTER_DATA_DIR = "/tmp/cinstall"


@pytest.mark.parametrize(
    "command, expected",
    [
        (
            {"clusters_install_data_directory": CLUSTER_DATA_DIR, "ocm_token": "123"},
            "'action' must be provided, supported actions: `('destroy', 'create')`",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "",
                "clusters": [TEST_CL],
            },
            "--ocm-token is required for clusters",
        ),
        (
            {"clusters_install_data_directory": CLUSTER_DATA_DIR, "action": "create", "ocm_token": "123"},
            "At least one '--cluster' option must be provided",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [{"platform": AWS_STR}],
            },
            "Cluster name or name_prefix must be provided",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [TEST_CL, {"name": "test-cl", "platform": AWS_STR}],
            },
            "Cluster names must be unique:",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config-file": "reg.json",
                "clusters": [{"name": "test-cl", "platform": AWS_STR, "acm": "true"}],
            },
            "The following keys must be booleans: ['acm']",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "docker_config_file": "dok.json",
                "registry_config_file": "reg.json",
                "ssh_key_file": "ssh.key",
                "clusters": [
                    {"name": "test-cl", "platform": AWS_STR, "region": "test-region", "log_level": "unsupported"}
                ],
            },
            "log levels are not supported for openshift-installer cli",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [TEST_CL],
            },
            "Registry config file is required for IPI cluster installations",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "docker_config_file": "",
                "registry_config_file": "reg.json",
                "clusters": [TEST_CL],
            },
            "Docker config file is required for IPI installations",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "docker_config_file": "dok.json",
                "registry_config_file": "reg.json",
                "ssh_key_file": "",
                "clusters": [TEST_CL],
            },
            "SSH file is required for IPI cluster installations",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "aws_secret_access_key": "",
                "aws_access_key_id": "",
                "clusters": [{"name": "test-cl", "platform": AWS_OSD_STR}],
            },
            "--aws-secret-access-key and --aws-access-key-id required for AWS OSD OR ACM cluster installations",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "aws_secret_access_key": "123",
                "aws_access_key_id": "123",
                "aws_account_id": "",
                "clusters": [{"name": "test-cl", "platform": AWS_OSD_STR}],
            },
            "--aws-account-id required for AWS OSD or Hypershift installations",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "aws_secret_access_key": "123",
                "aws_access_key_id": "123",
                "aws_account_id": "123",
                "clusters": [{"name": "test-cl", "platform": HYPERSHIFT_STR, "acm": True}],
            },
            f"ACM not supported for {HYPERSHIFT_STR} clusters",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [{"name": "test-cl", "platform": AWS_STR, "acm-clusters": "mycluser1"}],
            },
            "Managed ACM clusters: Cluster not found",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "registry_config_file": "reg.json",
                "clusters": [{"name": "test-cl", "platform": GCP_STR}],
            },
            "`--gcp-service-account-file` option must be provided for gcp-osd and gcp clusters",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "clusters": [
                    {
                        "name": "test-cl",
                        "platform": AWS_STR,
                        "acm-observability": True,
                        "acm-observability-storage-type": "bad",
                    }
                ],
            },
            "The following storage types are not supported for observability",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "aws_secret_access_key": "",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "aws-access-key-id": "",
                "clusters": [
                    {
                        "name": "test-cl",
                        "platform": AWS_STR,
                        "acm-observability": True,
                        "acm-observability-storage-type": S3_STR,
                    }
                ],
            },
            "The following clusters are missing storage data for observability:",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [{"name": "test-cl"}],
            },
            "is missing platform",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "clusters": [{"name": "test-cl", "platform": "unsupported"}],
            },
            "platform 'unsupported' is not supported",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "destroy_clusters_from_s3_bucket": True,
                "clusters": [{"name": "test-cl", "platform": "unsupported"}],
            },
            "`--s3-bucket-name` must be provided when running with",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "destroy_clusters_from_s3_bucket_query": True,
                "clusters": [{"name": "test-cl", "platform": "unsupported"}],
            },
            "`--s3-bucket-name` must be provided when running with",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "destroy_clusters_from_install_data_directory": True,
                "destroy_clusters_from_install_data_directory_using_s3_bucket": True,
                "clusters": [{"name": "test-cl", "platform": "unsupported"}],
            },
            "`--destroy-clusters-from-install-data-directory-using-s3-bucket` is not supported when running with `--destroy-clusters-from-install-data-directory`",
        ),
        (
            {
                "clusters_install_data_directory": "/directory/not/exists/never",
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "clusters": [{"name": "test-cl", "platform": "aws", "stream": "stable", "region": "reg1"}],
            },
            "Clusters data directory: /directory/not/exists/never is not writable",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "clusters": [{"name": "test-cl", "platform": "aws", "stream": "stable"}],
            },
            "Cluster region must be provided for the following clusters",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "clusters": [{"name": "test-cl", "platform": "aws", "stream": "bad-stream", "region": "reg1"}],
            },
            "aws platform does not support stream bad-stream, supported streams are ('stable', 'nightly', 'ec', 'ci', 'rc')",
        ),
        (
            {
                "clusters_install_data_directory": CLUSTER_DATA_DIR,
                "action": "create",
                "ocm_token": "123",
                "registry_config_file": "reg.json",
                "docker_config_file": "dok.json",
                "ssh_key_file": "ssh.key",
                "clusters": [{"name": "test-cl", "platform": "rosa", "channel-group": "bad-stream", "region": "reg1"}],
            },
            "rosa platform does not support channel-group bad-stream, supported channels are ('stable', 'candidate', 'nightly')",
        ),
    ],
)
def test_user_input(command, expected):
    command["dry_run"] = True

    with pytest.raises(UserInputError, match=re.escape(expected)):
        UserInput(**command)
