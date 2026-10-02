from __future__ import annotations

import io

import pytest

from latest_openshift.ui import UI, is_ci


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
    ],
)
def test_is_ci(value, expected):
    assert is_ci({"CI": value}) is expected


def test_is_ci_when_unset():
    assert is_ci({}) is False


def test_no_animation_without_a_terminal():
    ui = UI(stdout=io.StringIO(), stderr=io.StringIO())
    assert ui.animated is False
    assert ui.rich_stdout is False


def test_animation_on_a_terminal():
    ui = UI(stdout=FakeTTY(), stderr=FakeTTY())
    assert ui.animated is True
    assert ui.rich_stdout is True


def test_ci_suppresses_animation_even_on_a_terminal(monkeypatch):
    monkeypatch.setenv("CI", "true")
    ui = UI(stdout=FakeTTY(), stderr=FakeTTY())
    assert ui.animated is False
    assert ui.rich_stdout is False


def test_a_falsey_ci_variable_does_not_suppress_animation(monkeypatch):
    monkeypatch.setenv("CI", "false")
    ui = UI(stdout=FakeTTY(), stderr=FakeTTY())
    assert ui.animated is True


def test_values_go_to_stdout_and_chrome_goes_to_stderr():
    out, err = io.StringIO(), io.StringIO()
    ui = UI(stdout=out, stderr=err)

    ui.value("4.22.13")
    ui.info("resolving")
    ui.warn("careful")
    ui.error("broken")
    ui.success("done")

    assert out.getvalue() == "4.22.13\n"
    assert "resolving" in err.getvalue()
    assert "careful" in err.getvalue()
    assert "broken" in err.getvalue()


def test_quiet_silences_chrome_but_not_values():
    out, err = io.StringIO(), io.StringIO()
    ui = UI(stdout=out, stderr=err, quiet=True)

    ui.value("4.22.13")
    ui.info("resolving")
    ui.success("done")

    assert out.getvalue() == "4.22.13\n"
    assert err.getvalue() == ""


def test_tables_degrade_to_tab_separated_when_piped():
    out = io.StringIO()
    ui = UI(stdout=out, stderr=io.StringIO())

    ui.values([("4.22", "4.22.13"), ("4.21", "4.21.32")], headers=("STREAM", "LATEST"))

    assert out.getvalue() == "4.22\t4.22.13\n4.21\t4.21.32\n"


def test_tables_are_rendered_on_a_terminal():
    out = FakeTTY()
    ui = UI(stdout=out, stderr=FakeTTY())

    ui.values([("4.22", "4.22.13")], headers=("STREAM", "LATEST"))

    rendered = out.getvalue()
    assert "STREAM" in rendered
    assert "4.22.13" in rendered


def test_status_is_a_no_op_without_a_terminal():
    err = io.StringIO()
    ui = UI(stdout=io.StringIO(), stderr=err)

    with ui.status("working"):
        pass

    assert err.getvalue() == ""


def test_download_reporter_is_silent_without_a_terminal():
    err = io.StringIO()
    ui = UI(stdout=io.StringIO(), stderr=err)

    with ui.download("thing.tar.gz") as reporter:
        reporter.start(100)
        reporter.advance(100)

    assert err.getvalue() == ""
