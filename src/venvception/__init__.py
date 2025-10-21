#!/usr/bin/env python

import argparse as ap
import dataclasses as d
import os
import os.path as op
import pathlib as p
import subprocess as sp
import sys
import tomllib
import typing as t


class Inherit(t.TypedDict):
    inherit: str


PackageSpec = str
Dependencies = tuple[PackageSpec, ...]
TOMLDependencies = list[PackageSpec]
Package = tuple[PackageSpec, tuple[PackageSpec, ...]]
Tool = PackageSpec | Package
ToolGroup = list[Tool | Inherit]


class TOMLPackage(t.TypedDict):
    name: str
    dependencies: TOMLDependencies


TOMLTool = PackageSpec | TOMLPackage
TOMLToolGroup = list[TOMLTool | Inherit]


class VenvceptionException(RuntimeError):
    pass


def venvception(extras: list[str]):
    print("venvception v0.1.0", file=sys.stderr)
    venv = p.Path(os.environ.get("UV_PROJECT_ENVIRONMENT", op.join(os.getcwd(), ".venv")))
    if not venv.is_dir():
        raise VenvceptionException("Please create a local venv before running venvception.")

    xdg_data_project = venv / "share"
    xdg_data_project.mkdir(exist_ok=True)

    tools: set[Tool] = set()
    groups: dict[str, ToolGroup] = dict()

    toml_path = p.Path("pyproject.toml")
    if not toml_path.is_file():
        print("pyproject.toml not found. Nothing to do.", file=sys.stderr)
        return

    with toml_path.open("rb") as f:
        toml = tomllib.load(f)

    config = _load_config(toml)

    if "tools" in config:
        if not _is_toml_tool_group(config["tools"]):
            raise VenvceptionException("key 'tool.venvception.tools' is not a valid tool group.")

        tools = set(_toml_to_group(config["tools"], False))

    if "groups" in config:
        if not isinstance(config["groups"], dict):
            raise VenvceptionException("key 'tool.venvception.groups' is not a valid collection of tool groups.")

        for name, group in t.cast(dict[str, t.Any], config["groups"]).items():
            if not _is_toml_tool_group(group):
                raise VenvceptionException(f"key 'tool.venvception.tools.{name}' is not a valid tool group.")

            groups[name] = _toml_to_group(group, True)

    processed_groups: set[str] = set()
    for group_name in extras:
        tools, processed_groups = _process_group(group_name, groups, tools, processed_groups=processed_groups)

    for tool in tools:
        cmd = "uv tool "
        match tool:
            case tool if _is_package_spec(tool):
                cmd += tool
            case (name, dependencies):
                cmd += " ".join((f'--with "{dep}"' for dep in dependencies)) + " " + name
            case _:
                raise VenvceptionException("Can not happen.")
        _ = sp.run(
            cmd,
            shell=True,
            env=os.environ | {"XDG_DATA_HOME": str(xdg_data_project)},
            encoding="utf-8",
        )


def _load_config(toml: dict[str, t.Any]) -> dict[str, t.Any]:
    if "tool" not in toml:
        print("key 'tool' not found in pyproject.toml. Nothing to do.", file=sys.stderr)
        return {}

    if not isinstance(toml["tool"], dict):
        raise VenvceptionException("Key 'tool' in pyproject.toml exists but is not a dict.")

    if "venvception" not in toml["tool"]:
        print("key 'tool.venvception' not found in pyproject.toml. Nothing to do.", file=sys.stderr)
        return {}

    if not isinstance(toml["tool"]["venvception"], dict):
        raise VenvceptionException("key 'tool.venvception' in pyproject.toml exists but is not a dict.")

    return t.cast(dict[str, t.Any], toml["tool"]["venvception"])


def _is_package_spec(value: t.Any) -> t.TypeGuard[PackageSpec]:
    # TODO: Add version specifier check
    return isinstance(value, str)


@t.overload
def _toml_to_group(toml: TOMLToolGroup, inherit_allowed: t.Literal[True]) -> ToolGroup: ...


@t.overload
def _toml_to_group(toml: TOMLToolGroup, inherit_allowed: t.Literal[False]) -> set[Tool]: ...


def _toml_to_group(toml: TOMLToolGroup, inherit_allowed: bool) -> ToolGroup | set[Tool]:
    group = ToolGroup()
    for entry in toml:
        if _is_inherit(entry):
            if inherit_allowed:
                group.append(entry)
            else:
                raise VenvceptionException("Entry is an inherit but inherits are not allowed.")
        elif _is_toml_package(entry):
            group.append((entry["name"], tuple(entry["dependencies"])))
        else:
            group.append(t.cast(PackageSpec, entry))
    return group


def _is_inherit(value: t.Any) -> t.TypeGuard[Inherit]:
    return isinstance(value, dict) and "inherit" in value and isinstance(value["inherit"], str)


def _is_toml_dependencies(value: t.Any) -> t.TypeGuard[Dependencies]:
    return isinstance(value, list) and all(_is_package_spec(x) for x in t.cast(list[t.Any], value))


def _is_toml_package(value: t.Any) -> t.TypeGuard[TOMLPackage]:
    return (
        isinstance(value, dict)
        and "name" in value
        and _is_package_spec(value["name"])
        and "dependencies" in value
        and _is_toml_dependencies(value["dependencies"])
    )


def _is_toml_tool_group(value: t.Any) -> t.TypeGuard[TOMLToolGroup]:
    return isinstance(value, list) and all(
        _is_package_spec(entry) or _is_toml_package(entry) or _is_inherit(entry) for entry in t.cast(list[t.Any], value)
    )


def _is_toml_tool_groups(value: t.Any) -> t.TypeGuard[dict[str, TOMLToolGroup]]:
    return isinstance(value, dict) and all(
        isinstance(name, str) and _is_toml_tool_group(group)
        for name, group in t.cast(dict[t.Any, t.Any], value).items()
    )


def _process_group(
    group_name: str,
    groups: dict[str, ToolGroup],
    tools: set[Tool],
    processed_groups: set[str] | None = None,
) -> tuple[set[Tool], set[str]]:
    if group_name not in groups:
        raise VenvceptionException(f"Group {group_name} does not exist.")
    if processed_groups is not None and group_name in processed_groups:
        print(f"Group {group_name} already processed. Skipping.", file=sys.stderr)
        return (tools, processed_groups)

    if processed_groups is None:
        processed_groups = set()
    processed_groups.add(group_name)

    group = groups[group_name]
    for tool in group:
        if _is_inherit(tool):
            if tool["inherit"] not in groups:
                raise VenvceptionException("Included group does not exist.")
            tools, processed_groups = _process_group(tool["inherit"], groups, tools, processed_groups)
        else:
            tools.add(t.cast(Tool, tool))

    return tools, processed_groups


def main():
    parser = ap.ArgumentParser("venvception")
    parser.add_argument("extra", nargs="*", default=[])
    args = parser.parse_args(sys.argv[1:])

    try:
        venvception(args.extra)
    except VenvceptionException as e:
        print(f"ERROR: {str(e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
