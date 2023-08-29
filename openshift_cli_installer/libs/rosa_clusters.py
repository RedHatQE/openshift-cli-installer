import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import click
import rosa.cli
import yaml
from ocp_resources.job import Job
from python_terraform import IsNotFlagged, Terraform, TerraformCommandError

from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    HYPERSHIFT_STR,
    ROSA_STR,
)
from openshift_cli_installer.utils.helpers import (
    add_cluster_info_to_cluster_data,
    bucket_object_name,
    cluster_shortuuid,
    dump_cluster_data_to_file,
    get_cluster_object,
    get_manifests_path,
    tts,
    zip_and_upload_to_s3,
)


def remove_leftovers(res, cluster_data):
    leftovers = re.search(
        r"INFO: Once the cluster is uninstalled use the following commands to remove"
        r" the above "
        r"aws resources(.*?)INFO:",
        res.get("out", ""),
        re.DOTALL,
    )
    if leftovers:
        for line in leftovers.group(1).splitlines():
            _line = line.strip()
            if _line.startswith("rosa"):
                base_command = _line.split(maxsplit=1)[-1]
                command = base_command.replace("-c ", "--cluster=")
                command = command.replace("--prefix ", "--prefix=")
                command = command.replace("--oidc-config-id ", "--oidc-config-id=")
                rosa.cli.execute(
                    command=command,
                    ocm_client=cluster_data["ocm-client"],
                    aws_region=cluster_data["region"],
                )


def set_cluster_auth(cluster_data, cluster_object):
    auth_path = os.path.join(cluster_data["install-dir"], "auth")
    Path(auth_path).mkdir(parents=True, exist_ok=True)

    with open(os.path.join(auth_path, "kubeconfig"), "w") as fd:
        fd.write(yaml.dump(cluster_object.kubeconfig))

    with open(os.path.join(auth_path, "kubeadmin-password"), "w") as fd:
        fd.write(cluster_object.kubeadmin_password)


def wait_for_osd_cluster_ready_job(ocp_client):
    job = Job(
        client=ocp_client,
        name="osd-cluster-ready",
        namespace="openshift-monitoring",
    )
    job.wait_for_condition(
        condition=job.Condition.COMPLETE, status="True", timeout=tts(ts="1h")
    )


def create_oidc(cluster_data):
    oidc_prefix = cluster_data["cluster-name"]

    res = rosa.cli.execute(
        command=f"create oidc-config --managed=false --prefix={oidc_prefix}",
        aws_region=cluster_data["region"],
        ocm_client=cluster_data["ocm-client"],
    )
    oidc_id = re.search(r'"id": "([a-z0-9]+)",', res["out"])
    if not oidc_id:
        click.secho(
            f"Failed to get OIDC config for cluster {cluster_data['name']}", fg="red"
        )
        raise click.Abort()

    cluster_data["oidc-config-id"] = oidc_id.group(1)
    return cluster_data


def delete_oidc(cluster_data):
    rosa.cli.execute(
        command=f"delete oidc-config --oidc-config-id={cluster_data['oidc-config-id']}",
        aws_region=cluster_data["region"],
        ocm_client=cluster_data["ocm-client"],
    )


def terraform_init(cluster_data):
    aws_region = cluster_data["region"]
    # az_id example: us-east-2 -> ["use2-az1", "use2-az2"]
    az_id_prefix = "".join(re.match(r"(.*)-(\w).*-(\d)", aws_region).groups())
    cluster_parameters = {
        "aws_region": aws_region,
        "az_ids": [f"{az_id_prefix}-az1", f"{az_id_prefix}-az2"],
        "cluster_name": cluster_data["cluster-name"],
    }
    cidr = cluster_data.get("cidr")
    private_subnets = cluster_data.get("private_subnets")
    public_subnets = cluster_data.get("public_subnets")

    if cidr:
        cluster_parameters["cidr"] = cidr
    if private_subnets:
        cluster_parameters["private_subnets"] = private_subnets
    if public_subnets:
        cluster_parameters["public_subnets"] = public_subnets

    terraform = Terraform(
        working_dir=cluster_data["install-dir"], variables=cluster_parameters
    )
    terraform.init()
    return terraform


def destroy_hypershift_vpc(cluster_data):
    click.echo(f"Destroy hypershift VPC for cluster {cluster_data['name']}")
    terraform = terraform_init(cluster_data)
    terraform.destroy(
        force=IsNotFlagged,
        auto_approve=True,
        capture_output=True,
        raise_on_error=True,
    )


def prepare_hypershift_vpc(cluster_data):
    shutil.copy(
        os.path.join(get_manifests_path(), "setup-vpc.tf"), cluster_data["install-dir"]
    )
    terraform = terraform_init(cluster_data=cluster_data)
    try:
        click.echo(f"Preparing hypershift VPC for cluster {cluster_data['name']}")
        terraform.plan(dir_or_plan="rosa.plan")
        terraform.apply(capture_output=True, skip_plan=True, raise_on_error=True)
        terraform_output = terraform.output()
        private_subnet = terraform_output["cluster-private-subnet"]["value"]
        public_subnet = terraform_output["cluster-public-subnet"]["value"]
        cluster_data["subnet-ids"] = f'"{public_subnet},{private_subnet}"'
        return cluster_data
    except TerraformCommandError:
        click.secho(
            f"Create hypershift VPC for cluster {cluster_data['name']} failed, rolling"
            " back."
        )
        delete_oidc(cluster_data=cluster_data)
        # Clean up already created resources from the plan
        destroy_hypershift_vpc(cluster_data=cluster_data)
        raise


def prepare_managed_clusters_data(
    clusters,
    aws_account_id,
    aws_secret_access_key,
    aws_access_key_id,
):
    for _cluster in clusters:
        _cluster["cluster-name"] = _cluster["name"]
        _cluster["timeout"] = tts(ts=_cluster.get("timeout", "30m"))
        _cluster["channel-group"] = _cluster.get("channel-group", "stable")
        _cluster["aws-access-key-id"] = aws_access_key_id
        _cluster["aws-secret-access-key"] = aws_secret_access_key
        _cluster["aws-account-id"] = aws_account_id
        _cluster["multi-az"] = _cluster.get("multi-az", False)
        if _cluster["platform"] == HYPERSHIFT_STR:
            _cluster["hosted-cp"] = "true"
            _cluster["tags"] = "dns:external"
            _cluster["machine-cidr"] = _cluster.get("cidr", "10.0.0.0/16")

        expiration_time = _cluster.get("expiration-time")
        if expiration_time:
            _expiration_time = tts(ts=expiration_time)
            _cluster["expiration-time"] = (
                f"{(datetime.now() + timedelta(seconds=_expiration_time)).isoformat()}Z"
            )

    return clusters


def rosa_create_cluster(cluster_data, s3_bucket_name=None, s3_bucket_path=None):
    hosted_cp_arg = "--hosted-cp"
    _platform = cluster_data["platform"]
    ignore_keys = (
        "name",
        "platform",
        "ocm-env",
        "ocm-token",
        "install-dir",
        "timeout",
        "auth-dir",
        "cidr",
        "private_subnets",
        "public_subnets",
        "aws-access-key-id",
        "aws-secret-access-key",
        "aws-account-id",
        "multi-az",
        "ocm-client",
    )
    command = "create cluster --sts "

    if _platform == HYPERSHIFT_STR:
        cluster_data = create_oidc(cluster_data=cluster_data)
        cluster_data = prepare_hypershift_vpc(cluster_data=cluster_data)

    command_kwargs = {
        f"--{_key}={_val}"
        for _key, _val in cluster_data.items()
        if _key not in ignore_keys
    }

    for cmd in command_kwargs:
        if hosted_cp_arg in cmd:
            command += f"{hosted_cp_arg} "
        else:
            command += f"{cmd} "

    _shortuuid = cluster_shortuuid()
    cluster_data["s3_object_name"] = bucket_object_name(
        cluster_data=cluster_data, _shortuuid=_shortuuid, s3_bucket_path=s3_bucket_path
    )
    dump_cluster_data_to_file(cluster_data=cluster_data)

    try:
        rosa.cli.execute(
            command=command,
            ocm_client=cluster_data["ocm-client"],
            aws_region=cluster_data["region"],
        )

        cluster_object = get_cluster_object(cluster_data=cluster_data)
        cluster_object.wait_for_cluster_ready(wait_timeout=cluster_data["timeout"])
        set_cluster_auth(cluster_data=cluster_data, cluster_object=cluster_object)

        if _platform == ROSA_STR:
            wait_for_osd_cluster_ready_job(ocp_client=cluster_object.ocp_client)

        cluster_data = add_cluster_info_to_cluster_data(
            cluster_data=cluster_data, cluster_object=cluster_object
        )
        dump_cluster_data_to_file(cluster_data=cluster_data)

    except Exception as ex:
        click.secho(
            f"Failed to run cluster create for cluster {cluster_data['name']}\n{ex}",
            fg="red",
        )

        if s3_bucket_name and _platform == HYPERSHIFT_STR:
            zip_and_upload_to_s3(
                uuid=_shortuuid,
                install_dir=cluster_data["install-dir"],
                s3_bucket_name=s3_bucket_name,
                s3_bucket_path=s3_bucket_path,
            )
        rosa_delete_cluster(cluster_data=cluster_data)
        raise click.Abort()


def rosa_delete_cluster(cluster_data):
    base_cluster_data = None
    should_raise = False
    base_cluster_data_path = os.path.join(
        cluster_data["install-dir"], CLUSTER_DATA_YAML_FILENAME
    )

    if os.path.exists(base_cluster_data_path):
        with open(base_cluster_data_path) as fd:
            base_cluster_data = yaml.safe_load(fd.read())

        base_cluster_data.update(cluster_data)

    _cluster_data = base_cluster_data or cluster_data
    command = f"delete cluster --cluster={_cluster_data['cluster-name']}"
    try:
        res = rosa.cli.execute(
            command=command,
            ocm_client=cluster_data["ocm-client"],
            aws_region=cluster_data["region"],
        )
        cluster_object = get_cluster_object(cluster_data=_cluster_data)
        cluster_object.wait_for_cluster_deletion(wait_timeout=_cluster_data["timeout"])
        remove_leftovers(res=res, cluster_data=cluster_data)

    except rosa.cli.CommandExecuteError as ex:
        should_raise = ex

    if _cluster_data["platform"] == HYPERSHIFT_STR:
        destroy_hypershift_vpc(cluster_data=_cluster_data)
        delete_oidc(cluster_data=_cluster_data)

    if should_raise:
        click.secho(f"Failed to run cluster destroy\n{should_raise}", fg="red")
        raise click.Abort()


def rosa_check_existing_clusters(clusters, ocm_token):
    all_duplicate_cluster_names = []
    cluster_envs = set([_cluster["ocm-env"] for _cluster in clusters])
    for cluster_env in cluster_envs:
        deployed_clusters_names = {
            cluster["name"]
            for cluster in rosa.cli.execute(
                command="list clusters",
                aws_region="us-west-2",
                token=ocm_token,
                ocm_env=cluster_env,
            )["out"]
        }
        requested_clusters_name = {cluster["name"] for cluster in clusters}
        duplicate_cluster_names = deployed_clusters_names.intersection(
            requested_clusters_name
        )
        if duplicate_cluster_names:
            all_duplicate_cluster_names.extend(duplicate_cluster_names)

    if all_duplicate_cluster_names:
        click.secho(
            f"At least one cluster already exists: {all_duplicate_cluster_names}",
            fg="red",
        )
        raise click.Abort()
