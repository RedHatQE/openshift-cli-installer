import ast
import pytest


@pytest.fixture()
def user_options():
    user_options = []
    with open("openshift_cli_installer/cli.py", "r") as fd:
        tree = ast.parse(fd.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            for deco in node.decorator_list:
                if deco.func.attr != "option":
                    continue

                user_options.append([_arg.n for _arg in deco.args if _arg.n.startswith("--")][0])
    return user_options


@pytest.fixture()
def build_command_script():
    with open("openshift_cli_installer/scripts/openshift-cli-installer-build-command.py", "r") as fd:
        return fd.read()


def test_build_command_script(user_options, build_command_script):
    for user_option in user_options:
        if user_option in ("--dry-run", "--pdb"):
            continue

        assert user_option in build_command_script, f"User option {user_option} should be in build command script"
