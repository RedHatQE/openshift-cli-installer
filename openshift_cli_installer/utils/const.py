# General
import os

CLUSTER_DATA_YAML_FILENAME = "cluster_data.yaml"
USER_INPUT_CLUSTER_BOOLEAN_KEYS = ("acm", "acm-observability")
DESTROY_CLUSTERS_FROM_S3_BASE_DATA_DIRECTORY = os.path.join("/", "tmp", "openshift-cli-installer", "s3-extracted")

# Cluster types
AWS_STR = "aws"
ROSA_STR = "rosa"
AWS_OSD_STR = "aws-osd"
HYPERSHIFT_STR = "hypershift"
GCP_OSD_STR = "gcp-osd"
S3_STR = "s3"
SUPPORTED_PLATFORMS = (AWS_STR, ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR, GCP_OSD_STR)
AWS_BASED_PLATFORMS = (ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR, AWS_STR)
OCM_MANAGED_PLATFORMS = (ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR, GCP_OSD_STR)
OBSERVABILITY_SUPPORTED_STORAGE_TYPES = (S3_STR,)

# Cluster actions
DESTROY_STR = "destroy"
CREATE_STR = "create"
SUPPORTED_ACTIONS = (DESTROY_STR, CREATE_STR)

# OCM environments
PRODUCTION_STR = "production"
STAGE_STR = "stage"

# Timeouts
TIMEOUT_60MIN = "60m"

# Log colors
ERROR_LOG_COLOR = "red"
SUCCESS_LOG_COLOR = "green"
WARNING_LOG_COLOR = "yellow"
