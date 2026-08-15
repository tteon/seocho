"""Pin the CLI command tree and the CommandGroup extension seam.

Nothing previously called build_parser() from a test, so the largest UX
surface in the repo was refactorable only blind. These tests are the guard
that lets command groups migrate out of cli.py incrementally: the tree
snapshot fails loudly when a command appears, disappears, or moves.
"""

from __future__ import annotations

import argparse

import pytest

from seocho import cli
from seocho.cli import COMMAND_GROUPS, CommandGroup, LOCAL_COMMANDS, build_parser, register_group

EXPECTED_COMMANDS = {
    "add", "get", "search", "chat", "ask", "delete", "graphs", "doctor",
    "serve", "stop", "artifacts", "connect", "connectors", "new", "init",
    "index", "local-ask", "status", "compare", "experiment", "bundle",
    "ontology", "serve-http", "run", "sweep", "traces",
}

EXPECTED_ONTOLOGY_SUBCOMMANDS = {
    "check", "export", "diff", "report", "inspect-owl", "review",
    "datahub", "select-guardrail", "datahub-apply", "eval-answers", "import",
}


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("parser has no subparsers")


def test_command_tree_snapshot() -> None:
    top = _subparsers_action(build_parser())
    assert set(top.choices) == EXPECTED_COMMANDS


def test_ontology_group_snapshot() -> None:
    top = _subparsers_action(build_parser())
    ontology = _subparsers_action(top.choices["ontology"])
    assert set(ontology.choices) == EXPECTED_ONTOLOGY_SUBCOMMANDS


def test_local_commands_are_real_commands() -> None:
    top = _subparsers_action(build_parser())
    assert LOCAL_COMMANDS <= set(top.choices)


def test_unknown_command_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["no-such-command"])
    assert excinfo.value.code == 2


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0


def test_json_flag_convention() -> None:
    """Every subcommand with a JSON output switch accepts ``--json``.

    Historically 24 commands took ``--json`` while run/sweep/traces took only
    ``--output-json`` — whichever a new command copied decided its UX. The
    convention is now: dest ``output_json``, and ``--json`` always works.
    """
    top = _subparsers_action(build_parser())
    offenders = []
    for name, sub in top.choices.items():
        for action in sub._actions:
            if action.dest == "output_json" and "--json" not in action.option_strings:
                offenders.append(name)
    assert not offenders, f"commands whose JSON switch rejects --json: {offenders}"


@pytest.fixture
def toy_group():
    calls = {}

    def register(subparsers) -> None:
        parser = subparsers.add_parser("toy", help="toy group")
        toy_sub = parser.add_subparsers(dest="toy_command", required=True)
        echo = toy_sub.add_parser("echo")
        echo.add_argument("value")

    def handle(args: argparse.Namespace) -> int:
        if args.value == "explode":
            raise RuntimeError("toy exploded")
        calls["value"] = args.value
        return 0

    group = CommandGroup(name="toy", register=register, handle=handle)
    register_group(group)
    try:
        yield calls
    finally:
        COMMAND_GROUPS.pop("toy", None)


def test_registered_group_parses_and_dispatches(toy_group) -> None:
    assert cli.main(["toy", "echo", "hello"]) == 0
    assert toy_group == {"value": "hello"}


def test_registered_group_error_is_one_line_and_exit_1(toy_group, capsys) -> None:
    assert cli.main(["toy", "echo", "explode"]) == 1
    err = capsys.readouterr().err
    assert "toy exploded" in err
    assert "Traceback" not in err


def test_debug_flag_prints_traceback(toy_group, capsys) -> None:
    assert cli.main(["--debug", "toy", "echo", "explode"]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_duplicate_group_registration_is_refused(toy_group) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_group(CommandGroup(name="toy", register=lambda s: None, handle=lambda a: 1))
