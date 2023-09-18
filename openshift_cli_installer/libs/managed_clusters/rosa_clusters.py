import os
import re
import shutil

import click
import rosa.cli
import yaml
from ocm_python_wrapper.cluster import Cluster
from python_terraform import IsNotFlagged, Terraform

from openshift_cli_installer.utils.clusters import (
    add_cluster_info_to_cluster_data,
    dump_cluster_data_to_file,
    set_cluster_auth,
)
from openshift_cli_installer.utils.const import (
    CLUSTER_DATA_YAML_FILENAME,
    ERROR_LOG_COLOR,
    HYPERSHIFT_STR,
    SUCCESS_LOG_COLOR,
)
from openshift_cli_installer.utils.general import (
    get_manifests_path,
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
            f"Failed to get OIDC config for cluster {cluster_data['name']}",
            fg=ERROR_LOG_COLOR,
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
    click.echo(f"Destroy hypershift VPCs for cluster {cluster_data['name']}")
    terraform = terraform_init(cluster_data)
    rc, _, err = terraform.destroy(
        force=IsNotFlagged,
        auto_approve=True,
        capture_output=True,
    )
    if rc != 0:
        click.secho(
            f"Failed to destroy hypershift VPCs for cluster {cluster_data['name']} with"
            f" error: {err}"
        )
        raise click.Abort()


def prepare_hypershift_vpc(cluster_data):
    cluster_name = cluster_data["name"]
    shutil.copy(
        os.path.join(get_manifests_path(), "setup-vpc.tf"), cluster_data["install-dir"]
    )
    click.echo(f"Preparing hypershift VPCs for cluster {cluster_name}")
    terraform = terraform_init(cluster_data=cluster_data)
    terraform.plan(dir_or_plan="hypershift.plan")
    rc, _, err = terraform.apply(capture_output=True, skip_plan=True, auto_approve=True)
    if rc != 0:
        click.secho(
            f"Create hypershift VPC for cluster {cluster_name} failed with"
            f" error: {err}, rolling back.",
            fg=ERROR_LOG_COLOR,
        )
        delete_oidc(cluster_data=cluster_data)
        # Clean up already created resources from the plan
        destroy_hypershift_vpc(cluster_data=cluster_data)
        raise click.Abort()

    terraform_output = terraform.output()
    private_subnet = terraform_output["cluster-private-subnet"]["value"]
    public_subnet = terraform_output["cluster-public-subnet"]["value"]
    cluster_data["subnet-ids"] = f'"{public_subnet},{private_subnet}"'
    return cluster_data


def rosa_create_cluster(cluster_data):
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
        "shortuuid",
        "s3-object-name",
        "s3-bucket-name",
        "s3-bucket-path",
        "acm",
        "acm-clusters",
        "timeout-watch",
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

    dump_cluster_data_to_file(cluster_data=cluster_data)

    try:
        ocm_client = cluster_data["ocm-client"]
        rosa.cli.execute(
            command=command,
            ocm_client=ocm_client,
            aws_region=cluster_data["region"],
        )

        cluster_name = cluster_data["name"]
        cluster_object = Cluster(name=cluster_name, client=ocm_client)
        cluster_object.wait_for_cluster_ready(wait_timeout=cluster_data["timeout"])
        set_cluster_auth(cluster_data=cluster_data, cluster_object=cluster_object)

        cluster_data = add_cluster_info_to_cluster_data(
            cluster_data=cluster_data, cluster_object=cluster_object
        )
        dump_cluster_data_to_file(cluster_data=cluster_data)

        click.secho(
            f"Cluster {cluster_name} created successfully", fg=SUCCESS_LOG_COLOR
        )

    except Exception as ex:
        click.secho(
            f"Failed to run cluster create for cluster {cluster_data['name']}\n{ex}",
            fg=ERROR_LOG_COLOR,
        )

        rosa_delete_cluster(cluster_data=cluster_data)
        raise click.Abort()

    finally:
        s3_bucket_name = cluster_data.get("s3-bucket-name")
        if s3_bucket_name:
            zip_and_upload_to_s3(
                uuid=cluster_data["shortuuid"],
                install_dir=cluster_data["install-dir"],
                s3_bucket_name=s3_bucket_name,
                s3_bucket_path=cluster_data["s3-bucket-path"],
            )

    return cluster_data


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
    name = _cluster_data["cluster-name"]
    command = f"delete cluster --cluster={name}"
    try:
        ocm_client = _cluster_data["ocm-client"]
        res = rosa.cli.execute(
            command=command,
            ocm_client=ocm_client,
            aws_region=_cluster_data["region"],
        )
        cluster_object = Cluster(name=name, client=ocm_client)
        cluster_object.wait_for_cluster_deletion(wait_timeout=_cluster_data["timeout"])
        remove_leftovers(res=res, cluster_data=_cluster_data)

    except rosa.cli.CommandExecuteError as ex:
        should_raise = ex

    if _cluster_data["platform"] == HYPERSHIFT_STR:
        destroy_hypershift_vpc(cluster_data=_cluster_data)
        delete_oidc(cluster_data=_cluster_data)

    if should_raise:
        click.secho(
            f"Failed to run cluster destroy\n{should_raise}", fg=ERROR_LOG_COLOR
        )
        raise click.Abort()

    click.secho(f"Cluster {name} destroyed successfully", fg=SUCCESS_LOG_COLOR)
    return cluster_data
