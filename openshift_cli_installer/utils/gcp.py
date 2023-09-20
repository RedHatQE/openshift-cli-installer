from google.cloud import compute_v1
from google.oauth2 import service_account


# TODO: Move to https://github.com/RedHatQE/cloud-tools
def get_gcp_regions(gcp_service_account_file):
    credentials = service_account.Credentials.from_service_account_file(
        gcp_service_account_file
    )
    return [
        region.name
        for region in compute_v1.RegionsClient(credentials=credentials)
        .list(project=credentials.project_id)
        .items
    ]
