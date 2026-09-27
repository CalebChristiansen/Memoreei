"""The CLI's top-level options."""
from typer.testing import CliRunner

from memoreei.cli import app

runner = CliRunner()


def test_version_flag_prints_the_version():
    from memoreei import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"memoreei {__version__}"
