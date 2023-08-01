import json
import os
import shlex

import click
import yaml
from jinja2 import DebugUndefined, Environment, FileSystemLoader, meta
from ocp_utilities.utils import run_command
from utils.helpers import zip_and_upload_to_s3

# TODO: enable spot
"""
function inject_spot_instance_config() {
  local dir=${1}

  if [ ! -f /tmp/yq ]; then
    curl -L https://github.com/mikefarah/yq/releases/download/3.3.0/yq_linux_amd64 -o /tmp/yq && chmod +x /tmp/yq
  fi

  PATCH="${SHARED_DIR}/machinesets-spot-instances.yaml.patch"
  cat > "${PATCH}" << EOF
spec:
  template:
    spec:
      providerSpec:
        value:
          spotMarketOptions: {}
EOF

  for MACHINESET in $dir/openshift/99_openshift-cluster-api_worker-machineset-*.yaml; do
    /tmp/yq m -x -i "${MACHINESET}" "${PATCH}"
    echo "Patched spotMarketOptions into ${MACHINESET}"
  done

  echo "Enabled AWS Spot instances for worker nodes"
}
"""


def prepare_pull_secret(clusters, pull_secret):
    for cluster in clusters:
        pull_secret_file = os.path.join(cluster["auth-dir"], "pull-secret.json")
        with open(pull_secret_file, "w") as fd:
            fd.write(json.dumps(pull_secret))

        cluster["registry_config"] = pull_secret
        cluster["pull-secret-file"] = pull_secret_file


def create_install_config_file(clusters, registry_config_file, ssh_key_file):
    pull_secret = json.dumps(
        get_pull_secret_data(registry_config_file=registry_config_file)
    )
    for _cluster in clusters:
        install_dir = _cluster["install-dir"]
        _cluster["ssh_key"] = get_local_ssh_key(ssh_key_file=ssh_key_file)
        _cluster["pull_secret"] = pull_secret
        cluster_install_config = get_install_config_j2_template(cluster_dict=_cluster)

        with open(os.path.join(install_dir, "install-config.yaml"), "w") as fd:
            fd.write(yaml.dump(cluster_install_config))

    return clusters


def get_pull_secret_data(registry_config_file):
    with open(registry_config_file) as fd:
        return json.load(fd)


def get_local_ssh_key(ssh_key_file):
    with open(ssh_key_file) as fd:
        return fd.read().strip()


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


def download_openshift_install_binary(clusters, registry_config_file):
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

        rc, _, err = run_command(
            command=shlex.split(
                "oc adm release extract "
                f"quay.io/openshift-release-dev/ocp-release:{version}-x86_64 "
                f"--command={openshift_install_str} --to={binary_dir} --registry-config={registry_config_file}"
            ),
            check=False,
        )
        if not rc:
            click.echo(
                f"Failed to get {openshift_install_str} for version {version}, error: {err}"
            )
            raise click.Abort()

    return clusters


def create_or_destroy_aws_ipi_cluster(
    cluster_data, action, s3_bucket_name=None, s3_bucket_path=None, cleanup=False
):
    install_dir = cluster_data["install-dir"]
    binary_path = cluster_data["openshift-install-binary"]
    res, out, err = run_command(
        command=shlex.split(f"{binary_path} {action} cluster --dir {install_dir}"),
        capture_output=False,
        check=False,
    )
    if action == "create" and s3_bucket_name:
        zip_and_upload_to_s3(
            install_dir=install_dir,
            s3_bucket_name=s3_bucket_name,
            s3_bucket_path=s3_bucket_path,
        )

    if not res and not cleanup:
        click.echo(f"Failed to run cluster {action}\n\tERR: {err}\n\tOUT: {out}.")
        if action == "create":
            click.echo("Cleaning leftovers.")
            create_or_destroy_aws_ipi_cluster(
                cluster_data=cluster_data, action="destroy", cleanup=True
            )

    if not res:
        raise click.Abort()
