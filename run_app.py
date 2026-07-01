import logging

from payment_processor.app import main
from payment_processor.logging_setup import configure_logging


if __name__ == "__main__":
    configure_logging()
    try:
        main()
    except Exception:
        logging.getLogger(__name__).exception("Unhandled application error")
        raise
