import logging
from logging.handlers import TimedRotatingFileHandler


def test_configure_orchard_logger_uses_timed_rotating_file_handler(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    from orchard.logging import configure_orchard_logger, orchard_log_path

    logger = configure_orchard_logger(force=True)

    file_handlers = [handler for handler in logger.handlers if isinstance(handler, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].when == "MIDNIGHT"
    assert file_handlers[0].backupCount == 14
    assert orchard_log_path().exists()


def test_emit_log_keeps_console_output_and_writes_rotating_log(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCHARD_LOG_LEVEL", "debug")

    import orchard.cli as cli_mod
    from orchard.logging import configure_orchard_logger, get_orchard_logger, orchard_log_path

    configure_orchard_logger(force=True)
    cli_mod._CLI_LOGGER = get_orchard_logger("cli")

    cli_mod._emit_log("hello orchard logging", level="debug")

    out = capsys.readouterr().out
    assert "hello orchard logging" in out
    assert "hello orchard logging" in orchard_log_path().read_text(encoding="utf-8")
