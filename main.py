#!/usr/bin/env python

import argparse as ap
import dataclasses as d
import os
import os.path as op
import re
import subprocess as sp
import sys
import tomllib
import typing as t

import click

Command = t.Literal["install", "remove", "install_group", "remove_group", "list"]

Dependency = str
Dependencies = list[str]


class Package(t.TypedDict):
    name: str
    dependencies: Dependencies


class Include(t.TypedDict):
    include: str


Group = list[str | Package | Include]


def is_dependency(value: t.Any) -> t.TypeGuard[Dependency]:
    # TODO: Add version specifier check
    return isinstance(value, str)


def is_dependencies(value: t.Any) -> t.TypeGuard[Dependencies]:
    return isinstance(value, list) and all(is_dependency(x) for x in t.cast(list[t.Any], value))


def is_package(value: t.Any) -> t.TypeGuard[Package]:
    return (
        isinstance(value, dict)
        and "name" in value
        and isinstance(value["name"], str)
        and "dependencies" in value
        and is_dependencies(value["dependencies"])
    )


def is_include(value: t.Any) -> t.TypeGuard[Include]:
    return isinstance(value, dict) and "include" in value and isinstance(value["include"], str)



@t.final
class Venvception:
    def __init__(self, xdg_data_project: str, groups: dict[str, Group]):
        self.xdg_data_project = xdg_data_project
        self.groups = groups
        self.tools: set[tuple[str, tuple[str, ...]]] = set()
        stdout = self._uv("tool list", True, False)
        self.installed_tools = [
            tool.split(" ")[0].strip() for tool in stdout.split("\n") if tool != "" and not tool.startswith("-")
        ]
        self.installed_groups: set[str] = set()
        self.tools_dir = self._uv("tool dir", True, False)

    def install(self, tool: str, dependencies: tuple[str, ...]):
        if tool in self.installed_tools:
            print(f"Tool {tool} already installed.", file=sys.stderr)
            return

        self._uv(f"tool install {' '.join((f'--with "{dep}"' for dep in dependencies))} {tool}", False, False)

        if "pyelftools<=0.25" in dependencies:
            container_path = op.join(
                self.tools_dir,
                tool,
                "lib",
                f"python{sys.version_info.major}.{sys.version_info.minor}",
                "site-packages",
                "elftools",
                "construct",
                "lib",
                "container.py",
            )
            _ = sp.run(
                f"sed -i '5s/from collections import MutableMapping/from collections\\.abc import MutableMapping/' '{container_path}'",
                shell=True,
                stdout=sp.PIPE,
                stderr=sp.PIPE,
            )
            print(container_path)

    def remove(self, tool: str):
        if tool not in self.installed_tools:
            print(f"Tool {tool} not installed.", file=sys.stderr)
            return

        self._uv(f"tool uninstall {tool}", False, False)

    def install_group(self, groups: list[str]):
        for group in set(groups):
            self._accumulate_tools(group)

        for tool, dependencies in self.tools:
            self.install(tool, dependencies)

    def remove_group(self, groups: list[str]):
        for group in set(groups):
            self._accumulate_tools(group)

        for tool, _ in self.tools:
            self.remove(tool)

    def list(self, _: list[str]):
        if self.installed_tools:
            print("\n".join(self.installed_tools))

    def _accumulate_tools(self, group: str):
        if group not in self.groups:
            print(f"Group {group} not found.", file=sys.stderr)
            return
        elif group in self.installed_groups:
            print(f"Group {group} already installed.", file=sys.stderr)
            return

        self.installed_groups.add(group)

        for entry in self.groups[group]:
            if is_include(entry):
                self._accumulate_tools(entry["include"])
            elif is_package(entry):
                self.tools.add((entry["name"], tuple(entry["dependencies"])))
            elif is_dependency(entry):
                self.tools.add((entry, ()))
            else:
                print(f"Group entry {entry} is not valid. Skipping.", file=sys.stderr)

    @t.overload
    def _uv(self, cmd: str, capture_stdout: t.Literal[False], capture_stderr: t.Literal[False]) -> None: ...

    @t.overload
    def _uv(self, cmd: str, capture_stdout: t.Literal[True], capture_stderr: t.Literal[False]) -> str: ...

    @t.overload
    def _uv(self, cmd: str, capture_stdout: t.Literal[False], capture_stderr: t.Literal[True]) -> str: ...

    @t.overload
    def _uv(self, cmd: str, capture_stdout: t.Literal[True], capture_stderr: t.Literal[True]) -> tuple[str, str]: ...

    def _uv(self, cmd: str, capture_stdout: bool = False, capture_stderr: bool = False) -> str | tuple[str, str] | None:
        proc = sp.run(
            "uv " + cmd,
            shell=True,
            stdout=sp.PIPE if capture_stdout else None,
            stderr=sp.PIPE if capture_stderr else None,
            env=os.environ | {"XDG_DATA_HOME": self.xdg_data_project},
            encoding="utf-8",
        )

        if capture_stdout and capture_stderr:
            return (proc.stdout, proc.stderr)
        elif capture_stdout:
            return proc.stdout
        elif capture_stderr:
            return proc.stderr


if __name__ == "__main__":
    xdg_data_project = op.join(os.environ.get("UV_PROJECT_ENVIRONMENT", op.join(os.getcwd(), ".venv")), "share")
    if not op.isdir(xdg_data_project):
        print("Please create a local venv before running venvception.", file=sys.stderr)
        sys.exit(1)

    try:
        with open("pyproject.toml", "rb") as f:
            pyproject = tomllib.load(f)
    except:
        print("pyproject.toml could not be read.", file=sys.stderr)
        sys.exit(1)

    class VenvceptionConfig(t.TypedDict):
        tools: Group
        groups: dict[str, Group]

    tool_section = t.cast(dict[str, dict[str, VenvceptionConfig]] | None, pyproject.get("tool", None))
    if tool_section is None:
        # No tools to be installed; not an error
        sys.exit(0)

    config = t.cast(VenvceptionConfig | None, tool_section.get("venvception", None))
    if config is None:
        # No tools to be installed; not an error
        sys.exit(0)

    groups = config.get("groups", {})
    groups["default"] = config.get("tools", [])

    venvception = Venvception(xdg_data_project, groups)

    package_parser = ap.ArgumentParser(add_help=False)
    _ = package_parser.add_argument("package")

    parser = ap.ArgumentParser("venvception")
    subcmds = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")

    install_parser = subcmds.add_parser("install")
    _ = install_parser.add_argument("tool", metavar="TOOL")
    _ = install_parser.add_argument("dependencies", metavar="DEPENDENCIES", nargs="*")

    remove_parser = subcmds.add_parser("remove")
    _ = remove_parser.add_argument("tool", metavar="TOOL")

    install_group_parser = subcmds.add_parser("install_group")
    _ = install_group_parser.add_argument("group", metavar="GROUP", nargs="*", default=["default"])

    remove_group_parser = subcmds.add_parser("remove_group")
    _ = remove_group_parser.add_argument("group", metavar="GROUP", nargs="*", default=["default"])

    list_parser = subcmds.add_parser("list")

    @d.dataclass
    class Arguments:
        subcommand: Command = "install"
        group: list[str] = d.field(default_factory=list)
        tool: str = ""
        dependencies: list[str] = d.field(default_factory=list)

    args = Arguments()
    _ = parser.parse_args(sys.argv[1:], args)

    match args.subcommand:
        case "install":
            venvception.install(args.tool, tuple(args.dependencies))

        case "remove":
            venvception.remove(args.tool)

        case "install_group":
            venvception.install_group(args.group)

        case "remove_group":
            venvception.remove_group(args.group)

        case "list":
            venvception.list(args.group)
