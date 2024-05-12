from __future__ import annotations

# Taken from https://gist.github.com/welel/9cf860dd3f4d3e09f9b4305878b3a04e
from typing import Any, Dict

import click
from click import Parameter


class DictParamType(click.ParamType):
    """Represents the dictionary type of a CLI parameter.

    Validates and converts values from the command line string or Python into
    a Python dict.
        - All key-value pairs must be separated by one semicolon.
        - Key and value must be separated by one equal sign.
        - Converts sequences separeted by dots into a list: list value items
              must be separated by commas.
        - Converts numbers to int.

    Usage:
        >>> @click.option("--param", default=None, type=DictParamType())
        ... def command(param):
        ...     ...

        CLI: command --param='page=1; name=Items; rules=1, 2, three; extra=A,;'

    Example:

        >>> param_value = 'page=1; name=Items; rules=1, 2, three; extra=A,;'
        >>> DictParamType().convert(param_value, None, None)
        {'page': 1, 'name': 'Items', 'rules': [1, 2, 'three'], 'extra': ['A']}`

    """

    name = "dictionary"

    def convert(
        self,
        cli_value: Any,
        param: Parameter | None = None,
        ctx: click.Context | None = None,
    ) -> Dict[Any, Any]:
        """Converts CLI value to the dictionary structure.

        Args:
            cli_value (Any): The value to convert.
            param (click.Parameter | None): The parameter that is using this
                type to convert its value.
            ctx (click.Context | None): The current context that arrived
                at this value.

        Returns:
            dict: The validated and converted dictionary.

        Raises:
            click.BadParameter: If the validation is failed.
        """
        if isinstance(cli_value, dict):
            return cli_value

        pair = None
        try:
            keyvalue_pairs = cli_value.rstrip(";").split(";")
            result_dict = {}
            for pair in keyvalue_pairs:
                key, values = [item.strip() for item in pair.split("=")]
                converted_values = []
                for value in values.split(","):
                    value = value.strip()
                    if value.isdigit():
                        value = int(value)
                    converted_values.append(value)

                if len(converted_values) == 1:
                    result_dict[key] = converted_values[0]
                elif len(converted_values) > 1 and converted_values[-1] == "":
                    result_dict[key] = converted_values[:-1]
                else:
                    result_dict[key] = converted_values
            return result_dict
        except ValueError:
            self.fail(
                "All key-value pairs must be separated by one semicolon. "
                "Key and value must be separated by one equal sign. "
                "List value items must be separated by one comma. "
                f"Key-value: {pair}.",
                param,
                ctx,
            )
