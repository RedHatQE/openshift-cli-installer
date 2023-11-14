import contextlib
import os
import shlex

import pytest
from ocp_utilities.utils import run_command

from openshift_cli_installer.utils.const import ROSA_STR

BASE_COMMAND = "poetry run python openshift_cli_installer/cli.py --ocm-token=123456 --dry-run"


@contextlib.contextmanager
def unset_os_environment_variable(variables):
    exists_variables = []
    for variable_name in variables:
        variable_value = os.getenv(variable_name)
        if variable_value:
            exists_variables.append((variable_name, variable_value))
            os.environ.pop(variable_name, None)
    yield
    for variable_name in exists_variables:
        os.environ[variable_name[0]] = variable_name[1]


@pytest.mark.parametrize(
    "command, expected",
    [
        (BASE_COMMAND, "'action' must be provided"),
        (f"{BASE_COMMAND} --action invalid-action", "is not one of 'create', 'destroy'"),
        (
            (
                f"{BASE_COMMAND.replace('--ocm-token=123456', '')} "
                f"--action create --cluster 'name=test-cl;platform={ROSA_STR}'"
            ),
            "--ocm-token is required for clusters",
        ),
        (f"{BASE_COMMAND} --action create --cluster 'name=test-cl'", "is missing platform"),
        (
            (f"{BASE_COMMAND} --action create --cluster" " 'name=test-cl;platform=unsupported'"),
            "platform 'unsupported' is not supported",
        ),
    ],
    ids=["no-action", "invalid-action", "no-ocm-token", "cluster-missing-platform", "cluster-unsupported-platform"],
)
def test_user_input_negative(command, expected):
    with unset_os_environment_variable(variables=["OCM_TOKEN"]):
        rc, _, err = run_command(command=shlex.split(command), verify_stderr=False, check=False)

    if rc:
        raise pytest.fail(f"Command {command} should have failed but it didn't.")

    assert expected in err, f"Expected error: {expected} not found in {err}"
