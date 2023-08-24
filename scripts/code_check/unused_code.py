import ast
import os
import subprocess
import sys


def all_python_files():
    exclude_dirs = [".tox"]
    for root, _, files in os.walk(os.path.abspath(os.curdir)):
        if [_dir for _dir in exclude_dirs if _dir in root]:
            continue

        for filename in files:
            file_path = os.path.join(root, filename)
            if filename.endswith(".py") and file_path != os.path.abspath(__file__):
                yield file_path


def _iter_functions(tree):
    """
    Get all function from python file
    """

    def is_func(_elm):
        return isinstance(_elm, ast.FunctionDef)

    for elm in tree.body:
        if is_func(_elm=elm):
            yield elm


def get_unused_functions():
    _unused_functions = []
    for py_file in all_python_files():
        with open(py_file, "r") as fd:
            tree = ast.parse(source=fd.read())

        for func in _iter_functions(tree=tree):
            if func.name.startswith("test_"):
                continue
            func_docstring = ast.get_docstring(func) or ""
            if "### unused_code: ignore ###" in func_docstring:
                continue

            _used = subprocess.check_output(
                f"git grep -w '{func.name}' | wc -l", shell=True
            )
            used = int(_used.strip())
            if used < 2:
                _unused_functions.append(
                    f"{os.path.relpath(py_file)}:{func.name}:{func.lineno}:{func.col_offset} Is"
                    " not used anywhere in the code."
                )

    return _unused_functions


if __name__ == "__main__":
    unused_functions = get_unused_functions()
    if unused_functions:
        print("\n".join(unused_functions))
        sys.exit(1)
