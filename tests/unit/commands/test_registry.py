"""Tests for slash command registry."""

from cc.commands.registry import get_command


def test_skill_management_commands_registered() -> None:
    expected = {
        "skills": "__SKILLS__",
        "reload-skills": "__RELOAD_SKILLS__",
        "run-skill-generator": "__RUN_SKILL_GENERATOR__",
    }

    for name, marker in expected.items():
        command = get_command(name)
        assert command is not None
        assert command.handler() == marker
