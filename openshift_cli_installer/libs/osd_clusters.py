import click
from ocm_python_wrapper.cluster import Cluster, Clusters


def osd_check_existing_clusters(clusters, ocm_client):
    for _cluster in Clusters(client=ocm_client).get():
        duplicate_cluster_names = [
            cluster_data["name"]
            for cluster_data in clusters
            if cluster_data["name"] == _cluster.name
        ]
        if duplicate_cluster_names:
            click.secho(
                f"At least one cluster already exists: {duplicate_cluster_names}",
                fg="red",
            )
            raise click.Abort()


def osd_create_cluster(cluster_data):
    try:
        Cluster.provision_osd_aws(
            wait_for_ready=True,
            wait_timeout=cluster_data["timeout"],
            client=cluster_data["ocm-client"],
            name=cluster_data["name"],
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
        ).delete(wait=True, timeout=cluster_data["timeout"])
    except Exception as ex:
        click.secho(
            f"Failed to run cluster delete cluster {cluster_data['name']}\n{ex}",
            fg="red",
        )
        raise click.Abort()
