from payment_processor.logging_setup import configure_logging


def test_configure_logging_creates_log_file(tmp_path) -> None:
    log_path = tmp_path / "logs" / "payment_processor.log"

    result = configure_logging(log_path)

    assert result == log_path
    assert log_path.parent.exists()
