# General
CLUSTER_DATA_YAML_FILENAME = "cluster_data.yaml"

# Cluster types
AWS_STR = "aws"
ROSA_STR = "rosa"
AWS_OSD_STR = "aws-osd"
HYPERSHIFT_STR = "hypershift"
GCP_OSD_STR = "gcp-osd"
SUPPORTED_PLATFORMS = (AWS_STR, ROSA_STR, HYPERSHIFT_STR, AWS_OSD_STR, GCP_OSD_STR)

# Cluster actions
DESTROY_STR = "destroy"
CREATE_STR = "create"

# OCM environments
PRODUCTION_STR = "production"
STAGE_STR = "stage"

# Timeouts
TIMEOUT_60MIN = "60m"

# Log colors
ERROR_LOG_COLOR = "red"
SUCCESS_LOG_COLOR = "green"
WARNING_LOG_COLOR = "yellow"
