from django.core.management.base import BaseCommand
from datetime import datetime, date
import logging
from PB_Assistant.apps.textprocessing.pdf_processor import PdfProcessor
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process papers for a given year range and planetary boundaries.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--last-year',
            action='store_true',
            help="Process data for the previous calendar year."
        )
        parser.add_argument(
            '--start-year', '-sy',
            type=int,
            help="Start year for processing."
        )
        parser.add_argument(
            '--end-year', '-ey',
            type=int,
            help="End year for processing (exclusive). Defaults to current year."
        )
        parser.add_argument(
            '--pb-names',
            type=str,
            nargs='+',
            help="List of planetary boundary short names to filter by."
        )
        parser.add_argument("--no-embed", action="store_true", help="Do not run embedding after fulltext insert")

    def handle(self, *args, **options):

        no_embed: bool = options["no_embed"]
        # Validate input
        if not options['last_year'] and not options['start_year']:
            self.stderr.write(self.style.ERROR(
                "You must specify either --last-year or --start-year."))
            return

        # Determine year range
        if options['last_year']:
            current_year = date.today().year
            start_year = current_year - 1
            end_year = current_year
        else:
            start_year = options['start_year']
            end_year = options.get('end_year') or datetime.now().year

        if end_year < start_year:
            logger.error(f"Invalid range: start_year ({start_year}) must be less than end_year ({end_year})")
            return

        pb_names = options.get('pb_names') or []

        logger.info(f"Starting processing for PBs {pb_names or 'ALL'} from {start_year} to {end_year}")

        pdf_processor= PdfProcessor(pb_names, batch_size=30, max_papers=30, no_embed=no_embed)

        for year in range(start_year, end_year+1):
            logger.info(f"Processing papers for year {year}")
            pdf_processor.process_academic_papers(year, year)
