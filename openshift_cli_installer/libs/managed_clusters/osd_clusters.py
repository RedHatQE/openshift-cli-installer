import click
from ocm_python_wrapper.cluster import Cluster

from openshift_cli_installer.utils.clusters import (
    add_cluster_info_to_cluster_data,
    dump_cluster_data_to_file,
    set_cluster_auth,
)
from openshift_cli_installer.utils.const import (
    AWS_OSD_STR,
    ERROR_LOG_COLOR,
    GCP_OSD_STR,
    SUCCESS_LOG_COLOR,
)
from openshift_cli_installer.utils.general import zip_and_upload_to_s3


def osd_create_cluster(cluster_data):
    cluster_object = Cluster(
        client=cluster_data["ocm-client"],
        name=cluster_data["name"],
    )
    try:
        cluster_platform = cluster_data["platform"]
        ocp_version = (
            cluster_data["version"]
            if cluster_data["channel-group"] != "candidate"
            else f"{cluster_data['version']}-candidate"
        )
        provision_osd_kwargs = {
            "wait_for_ready": True,
            "wait_timeout": cluster_data["timeout"],
            "region": cluster_data["region"],
            "ocp_version": ocp_version,
            "replicas": cluster_data["replicas"],
            "compute_machine_type": cluster_data["compute-machine-type"],
            "multi_az": cluster_data["multi-az"],
            "channel_group": cluster_data["channel-group"],
            "expiration_time": cluster_data.get("expiration-time"),
            "platform": cluster_platform.replace("-osd", ""),
        }
        if cluster_platform == AWS_OSD_STR:
            provision_osd_kwargs.update(
                {
                    "aws_access_key_id": cluster_data["aws-access-key-id"],
                    "aws_account_id": cluster_data["aws-account-id"],
                    "aws_secret_access_key": cluster_data["aws-secret-access-key"],
                }
            )
        elif cluster_platform == GCP_OSD_STR:
            provision_osd_kwargs.update(
                {"gcp_service_account": cluster_data["gcp_service_account"]}
            )

        cluster_object.provision_osd(**provision_osd_kwargs)

        cluster_data = add_cluster_info_to_cluster_data(
            cluster_data=cluster_data,
            cluster_object=cluster_object,
        )
        dump_cluster_data_to_file(cluster_data=cluster_data)
        set_cluster_auth(cluster_data=cluster_data, cluster_object=cluster_object)

        click.secho(
            f"Cluster {cluster_data['name']} created successfully", fg=SUCCESS_LOG_COLOR
        )

    except Exception as ex:
        click.secho(
            f"Failed to run cluster create for cluster {cluster_data['name']}\n{ex}",
            fg=ERROR_LOG_COLOR,
        )

        osd_delete_cluster(cluster_data=cluster_data)
        raise click.Abort()

    finally:
        s3_bucket_name = cluster_data.get("s3-bucket-name")
        if s3_bucket_name:
            zip_and_upload_to_s3(
                install_dir=cluster_data["install-dir"],
                s3_bucket_name=s3_bucket_name,
                s3_bucket_path=cluster_data["s3-bucket-path"],
                uuid=cluster_data["shortuuid"],
            )

    return cluster_data


def osd_delete_cluster(cluster_data):
    name = cluster_data["name"]

    try:
        Cluster(
            client=cluster_data["ocm-client"],
            name=name,
        ).delete(timeout=cluster_data["timeout"])
        click.secho(f"Cluster {name} destroyed successfully", fg=SUCCESS_LOG_COLOR)
        return cluster_data
    except Exception as ex:
        click.secho(
            f"Failed to run cluster delete cluster {name}\n{ex}",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()
