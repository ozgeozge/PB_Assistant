from django.db.utils import IntegrityError
from django.conf import settings

import importlib
from datetime import date, timedelta
import logging
import time

logger = logging.getLogger(__name__)
from PB_Assistant.models import PlanetaryBoundary, BoundaryQuery, Source

class Fetcher:
    """Loads articles from various sources and stores them in a database."""

    def __init__(self):
        self.sources = []

        # Instantiate all sources defined in the settings
        for source in settings.FETCHING_SOURCES:
            # Split into module and class
            m, c = source.rsplit('.', 1)

            # Import module and instantiate class
            module = importlib.import_module(m)
            class_ = getattr(module, c)
            api_key = settings.FETCHING_API_KEYS_BY_SOURCE.get(c)
            instance = class_(api_key=api_key)
            self.add_source(instance)

    def add_source(self, source):
        self.sources.append(source)

    def download_by_year(self, pb_names, start_year=None, end_year=None):
        """Searches all sources for the given query string. Stores articles
        in the database. The searched time span can be limited via
        `start_year` and `end_year`."""
        if len(pb_names) > 0:
            planetary_boundaries =PlanetaryBoundary.objects.filter(short_name__in=pb_names)
        else:
            # if any planetary boundary name is not specified in the command, then fetch for all boundaries
            logger.warning("No planetary boundary name specified, fetching for all planetary boundaries...")
            planetary_boundaries=PlanetaryBoundary.objects.all()

        if planetary_boundaries.count() == 0:
            logger.error("No valid planetary boundary name specified.")
            return
        t0 = time.time()
        for pb in planetary_boundaries:
            for s in self.sources:
                class_name=s.__class__.__name__
                source = Source.objects.filter(name__iexact=class_name).first()
                if not source:
                    logger.error('Skipping fetching from {}. No valid source found.'.format(class_name))
                    continue
                query_obj = BoundaryQuery.objects.filter(planetary_boundary=pb, source=source).first()
                if not query_obj or not query_obj.query:
                    logger.error('Skipping fetching from {}. No valid query string specified.'.format(class_name))
                    continue
                query_string= query_obj.query
                logger.info('Querying {} for "{} start date: {} end date: {}" ...'.format(class_name, pb.name, start_year,end_year))
                s.download(pb, query_string, start_year, end_year)
        elapsed_time = time.time() - t0
        logger.info(f"Elapsed time for fetching sources: {round(elapsed_time,2)} seconds.")

