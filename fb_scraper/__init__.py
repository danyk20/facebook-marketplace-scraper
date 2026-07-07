import logging

__version__ = "0.2.0"

# Library code (fb_scraper.browser, fb_scraper.scraper) only ever logs
# through loggers under this "fb_scraper" namespace - it never calls
# basicConfig or attaches handlers of its own (that would be rude to a host
# application). fb_scraper.cli is the only place that sets up real handlers
# (see its _configure_cli_logging()), so plain library use is silent unless
# the caller configures logging themselves, e.g.:
#     import logging; logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).addHandler(logging.NullHandler())
