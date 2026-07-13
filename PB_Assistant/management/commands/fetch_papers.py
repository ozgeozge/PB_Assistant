from datetime import datetime, date, timedelta
from argparse import ArgumentTypeError
from django.core.management.base import BaseCommand, CommandError
from django.db.utils import IntegrityError
from PB_Assistant.apps.ingestion.fetcher import Fetcher
import logging
logger = logging.getLogger(__name__)

class Command(BaseCommand):

    def add_arguments(self, parser):
  
        parser.add_argument(
            '--last-year',
            action='store_true',
            help=""
        )
        parser.add_argument(
            '--start-year', '-sy',
            type=int,

        )
        parser.add_argument(
            '--end-year', '-ey',
            type=int,

        )
        parser.add_argument('--pb-names', type=str, nargs='+',
                            help="""A list of PBs for which to fetch papers. If not specified, all PBs will be fetched."""
        )


    def handle(self, *args, **options):
        """The main entry point for this command."""

        fetcher = Fetcher()

        pb_names = options['pb_names'] if options['pb_names'] else []
        # IMPORTANT NOTE: If NO PB name is specified, it fetches papers for all PBs!!!

        if options['last_year']:
            start_year= date.today().year-1
            end_year= date.today().year
            fetcher.download_by_year(pb_names, start_year, end_year)

        elif options['start_year']:
            start_year = options['start_year']
            if options['end_year']:
                end_year = options['end_year']
            else:
                end_year = date.today().year

            if end_year < start_year:
                self.stderr.write("The start date must lie before the end date.")
                return

            for year in range(start_year, end_year+1):
                logger.info(f"Processing papers for year {year}")
                fetcher.download_by_year(pb_names, start_year=year, end_year=year)
            return

        else:
            self.stderr.write("Nothing was fetched. No time span was specified.")
