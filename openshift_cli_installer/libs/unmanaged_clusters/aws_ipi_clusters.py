import functools
import json
import shlex

import click
import yaml
from jinja2 import DebugUndefined, Environment, FileSystemLoader, meta
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.cluster_versions import set_clusters_versions
from openshift_cli_installer.utils.const import ERROR_LOG_COLOR
from openshift_cli_installer.utils.general import get_manifests_path

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


def generate_unified_pull_secret(registry_config_file, docker_config_file):
    registry_config = get_pull_secret_data(registry_config_file=registry_config_file)
    docker_config = get_pull_secret_data(registry_config_file=docker_config_file)
    docker_config["auths"].update(registry_config["auths"])

    return json.dumps(docker_config)


def get_pull_secret_data(registry_config_file):
    with open(registry_config_file) as fd:
        return json.load(fd)


def get_local_ssh_key(ssh_key_file):
    with open(ssh_key_file) as fd:
        return fd.read().strip()


def get_install_config_j2_template(cluster_dict):
    env = Environment(
        loader=FileSystemLoader(get_manifests_path()),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=DebugUndefined,
    )

    template = env.get_template(name="install-config-template.j2")
    rendered = template.render(cluster_dict)
    undefined_variables = meta.find_undeclared_variables(env.parse(rendered))
    if undefined_variables:
        click.secho(
            f"The following variables are undefined: {undefined_variables}",
            fg=ERROR_LOG_COLOR,
        )
        raise click.Abort()

    return yaml.safe_load(rendered)


@functools.cache
def get_aws_versions():
    versions_dict = {}
    for source_repo in (
        "quay.io/openshift-release-dev/ocp-release",
        "registry.ci.openshift.org/ocp/release",
    ):
        versions_dict[source_repo] = run_command(
            command=shlex.split(f"regctl tag ls {source_repo}"),
            check=False,
        )[1].splitlines()

    return versions_dict


def update_aws_clusters_versions(clusters, _test=False):
    for _cluster_data in clusters:
        _cluster_data["stream"] = _cluster_data.get("stream", "stable")

    base_available_versions = get_all_versions(_test=_test)

    return set_clusters_versions(
        clusters=clusters,
        base_available_versions=base_available_versions,
    )


def get_all_versions(_test=None):
    if _test:
        with open("openshift_cli_installer/tests/all_aws_versions.json") as fd:
            base_available_versions = json.load(fd)
    else:
        base_available_versions = get_aws_versions()

    return base_available_versions
