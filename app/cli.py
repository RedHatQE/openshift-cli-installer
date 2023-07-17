import json
import multiprocessing
import os
import shlex
import shutil
from pathlib import Path

import click
import shortuuid
import yaml
from click_dict_type import DictParamType
from clouds.aws.aws_utils import set_and_verify_aws_credentials
from clouds.aws.session_clients import s3_client
from jinja2 import DebugUndefined, Environment, FileSystemLoader, meta
from ocp_utilities.utils import run_command


class RunInstallUninstallCommandError(Exception):
    def __init__(self, action, out, err):
        self.action = action
        self.out = out
        self.err = err

    def __str__(self):
        return f"Failed to run cluster {self.action}\nERR: {self.err}\nOUT: {self.out}"


def get_install_config_j2_template(cluster_dict):
    template_file = "install-config-template.j2"
    env = Environment(
        loader=FileSystemLoader("app/manifests/"),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=DebugUndefined,
    )

    template = env.get_template(name=template_file)
    rendered = template.render(cluster_dict)
    undefined_variables = meta.find_undeclared_variables(env.parse(rendered))
    if undefined_variables:
        click.echo(f"The following variables are undefined: {undefined_variables}")
        raise click.Abort()

    return yaml.safe_load(rendered)


def generate_cluster_dir_path(clusters, base_directory):
    for _cluster in clusters:
        _cluster["install-dir"] = os.path.join(
            base_directory, _cluster["platform"], _cluster["name"]
        )
    return clusters


def verify_processes_passed(processes, action):
    failed_processes = {}

    for _proc in processes:
        _proc.join()
        if _proc.exitcode != 0:
            failed_processes[_proc.name] = _proc.exitcode

    if failed_processes:
        click.echo(f"Some jobs failed to {action}: {failed_processes}\n")
        raise click.Abort()


def download_openshift_install_binary(clusters, pull_secret_file):
    versions = set()
    openshift_install_str = "openshift-install"

    for cluster in clusters:
        versions.add(cluster["version"])

    for version in versions:
        binary_dir = os.path.join("/tmp", version)
        clusters = [_cluster for _cluster in clusters if _cluster["version"] == version]
        for cluster in clusters:
            cluster["openshift-install-binary"] = os.path.join(
                binary_dir, openshift_install_str
            )

        run_command(
            command=shlex.split(
                "oc adm release extract "
                f"quay.io/openshift-release-dev/ocp-release:{version}-x86_64 "
                f"--command={openshift_install_str} --to={binary_dir} --registry-config={pull_secret_file}"
            ),
            check=False,
        )

    return clusters


def create_or_destroy_cluster(
    cluster_data, action, s3_bucket_name=None, s3_bucket_path=None
):
    directory = cluster_data["install-dir"]
    binary_path = cluster_data["openshift-install-binary"]
    res, out, err = run_command(
        command=shlex.split(f"{binary_path} {action} cluster --dir {directory}"),
        capture_output=False,
        check=False,
    )
    if action == "create" and s3_bucket_name:
        zip_file = shutil.make_archive(
            base_name=f"{directory}-{shortuuid.uuid()}",
            format="zip",
            root_dir=directory,
        )
        s3_client().upload_file(
            Filename=zip_file,
            Bucket=s3_bucket_name,
            Key=os.path.join(s3_bucket_path or "", os.path.split(zip_file)[-1]),
        )
    if not res:
        raise RunInstallUninstallCommandError(action=action, out=out, err=err)


def create_openshift_cluster(cluster_data, s3_bucket_name, s3_bucket_path):
    create_or_destroy_cluster(
        cluster_data=cluster_data,
        action="create",
        s3_bucket_name=s3_bucket_name,
        s3_bucket_path=s3_bucket_path,
    )


def destroy_openshift_cluster(cluster_data):
    create_or_destroy_cluster(cluster_data=cluster_data, action="destroy")


def create_install_config_file(clusters, pull_secret_file):
    ssh_key = get_local_ssh_key()
    pull_secret = json.dumps(get_pull_secret_data(pull_secret_file=pull_secret_file))
    for _cluster in clusters:
        install_dir = _cluster["install-dir"]
        _cluster["pull_secret"] = pull_secret
        _cluster["ssh_key"] = ssh_key

        cluster_install_config = get_install_config_j2_template(cluster_dict=_cluster)
        Path(install_dir).mkdir(parents=True, exist_ok=True)

        with open(os.path.join(install_dir, "install-config.yaml"), "w") as fd:
            fd.write(yaml.dump(cluster_install_config))

    return clusters


def get_pull_secret_data(pull_secret_file):
    with open(pull_secret_file) as fd:
        return json.load(fd)


def get_local_ssh_key():
    with open(os.path.expanduser("~/.ssh/id_rsa.pub")) as fd:
        return fd.read().strip()


@click.command()
@click.option(
    "-a",
    "--action",
    type=click.Choice(["create", "destroy"]),
    help="Action to perform Openshift cluster/s",
)
@click.option(
    "-p",
    "--parallel",
    help="Run clusters install/uninstall in parallel",
    is_flag=True,
    show_default=True,
)
@click.option(
    "--clusters-install-data-directory",
    help="""
\b
Path to cluster install data
    For install this will be used to store the install data.
    For uninstall this will be used to uninstall the cluster.
""",
    default=os.environ.get(
        "CLUSTER_INSTALL_DATA_DIRECTORY",
        "/openshift-cli-installer/clusters-install-data",
    ),
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--pull-secret-file",
    help="Path to pull secret json file, can be obtained from console.redhat.com",
    required=True,
    default=os.environ.get("PULL_SECRET"),
    type=click.Path(exists=True),
    show_default=True,
)
@click.option(
    "--s3-bucket-name",
    help="S3 bucket name to store install folder backups",
    show_default=True,
)
@click.option(
    "--s3-bucket-path",
    help="S3 bucket path to store the backups",
    show_default=True,
)
@click.option(
    "-c",
    "--cluster",
    type=DictParamType(),
    help="""
\b
Cluster/s to install.
Format to pass is:
    'name=cluster1;base_domain=aws.domain.com;platform=aws;region=us-east-2;version=4.14.0-ec.2'
Required parameters:
    name: Cluster name.
    base_domain: Base domain for the cluster.
    platform: Cloud platform to install the cluster on. (Currently only AWS supported).
    region: Region to use for the cloud platform.
    version: Openshift cluster version to install

Check install-config-template.j2 for variables that can be overwritten by the user.
For example:
    fips=true
    worker_flavor=m5.xlarge
    worker_replicas=6
    """,
    required=True,
    multiple=True,
)
def main(
    action,
    pull_secret_file,
    parallel,
    cluster,
    clusters_install_data_directory,
    s3_bucket_name,
    s3_bucket_path,
):
    """
    Create/Destroy Openshift cluster/s
    """
    _aws_region = None
    # Set _aws_region with one of the used regions specified
    for cluster_config in cluster:
        _aws_region = cluster_config.get("region")
        if _aws_region:
            break
    set_and_verify_aws_credentials(region_name=_aws_region)

    clusters = generate_cluster_dir_path(
        clusters=cluster, base_directory=clusters_install_data_directory
    )

    clusters = download_openshift_install_binary(
        clusters=clusters, pull_secret_file=pull_secret_file
    )

    create = action == "create"
    if create:
        clusters = create_install_config_file(
            clusters=cluster, pull_secret_file=pull_secret_file
        )

    processes = []
    kwargs = {}
    if create:
        action_func = create_openshift_cluster
        kwargs.update(
            {"s3_bucket_name": s3_bucket_name, "s3_bucket_path": s3_bucket_path}
        )
    else:
        action_func = destroy_openshift_cluster

    for _cluster in clusters:
        kwargs["cluster_data"] = _cluster
        if parallel:
            proc = multiprocessing.Process(
                name=f"{_cluster['name']}---{action}",
                target=action_func,
                kwargs=kwargs,
            )
            processes.append(proc)
            proc.start()

        else:
            action_func(**kwargs)

    if processes:
        verify_processes_passed(processes=processes, action=action)


if __name__ == "__main__":
    main()
