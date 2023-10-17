import shlex

import pytest
from ocp_utilities.utils import run_command

BASE_COMMAND = "poetry run python openshift_cli_installer/cli.py --dry-run"


@pytest.mark.parametrize(
    "command, expected",
    [
        (
            BASE_COMMAND,
            "'action' must be provided",
        ),
        (
            f"{BASE_COMMAND} --action invalid-action",
            "is not one of 'create', 'destroy'",
        ),
    ],
    ids=[
        "no-action",
        "invalid-action",
    ],
)
def test_user_input_negative(command, expected):
    rc, _, err = run_command(
        command=shlex.split(command), verify_stderr=False, check=False
    )
    if rc:
        raise pytest.fail(f"Command {command} should have failed but it didn't.")
    assert expected in err, f"Expected error: {expected} not found in {err}"
