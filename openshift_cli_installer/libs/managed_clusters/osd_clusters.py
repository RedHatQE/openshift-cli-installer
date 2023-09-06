import click
from ocm_python_wrapper.cluster import Cluster

from openshift_cli_installer.utils.clusters import (
    add_cluster_info_to_cluster_data,
    dump_cluster_data_to_file,
    set_cluster_auth,
)


def osd_create_cluster(cluster_data):
    cluster_object = Cluster(
        client=cluster_data["ocm-client"],
        name=cluster_data["name"],
    )
    try:
        cluster_object.provision_osd_aws(
            wait_for_ready=True,
            wait_timeout=cluster_data["timeout"],
            region=cluster_data["region"],
            ocp_version=cluster_data["version"],
            access_key_id=cluster_data["aws-access-key-id"],
            account_id=cluster_data["aws-account-id"],
            secret_access_key=cluster_data["aws-secret-access-key"],
            replicas=cluster_data["replicas"],
            compute_machine_type=cluster_data["compute-machine-type"],
            multi_az=cluster_data["multi-az"],
            channel_group=cluster_data["channel-group"],
            expiration_time=cluster_data.get("expiration-time"),
        )

        cluster_data = add_cluster_info_to_cluster_data(
            cluster_data=cluster_data,
            cluster_object=cluster_object,
        )
        dump_cluster_data_to_file(cluster_data=cluster_data)
        set_cluster_auth(cluster_data=cluster_data, cluster_object=cluster_object)

    except Exception as ex:
        click.secho(
            f"Failed to run cluster create for cluster {cluster_data['name']}\n{ex}",
            fg="red",
        )

        osd_delete_cluster(cluster_data=cluster_data)
        raise click.Abort()

    click.echo(f"Cluster {cluster_data['name']} created successfully")


def osd_delete_cluster(cluster_data):
    try:
        Cluster(
            client=cluster_data["ocm-client"],
            name=cluster_data["name"],
        ).delete(timeout=cluster_data["timeout"])
    except Exception as ex:
        click.secho(
            f"Failed to run cluster delete cluster {cluster_data['name']}\n{ex}",
            fg="red",
        )
        raise click.Abort()
