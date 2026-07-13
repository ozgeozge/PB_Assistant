import uuid
from datetime import date, datetime
from typing import Any, Generator, Literal, TypeVar
import re
from .abstractsource import AbstractAPI
from PB_Assistant.models import AcademicPaper
from PB_Assistant.apps.ingestion.sources.data_models import (
    AcademicPaperData
)
from .util import clear_empty, as_uuid, RequestClient, get, get_value


Database = Literal['WOS', 'BCI', 'BIOABS', 'BIOSIS', 'CCC', 'DIIDW', 'DRCI', 'MEDLINE', 'PPRN', 'WOK', 'ZOOREC']


SUPPORTED_FIELD_TAGS = (
    'TI', 'IS', 'SO', 'VL', 'PG', 'CS', 'PY', 'FPY', 'DOP', 'AU', 'AI',
    'UT', 'DO', 'DT', 'PMID', 'OG', 'TS', 'SUR'
)


def normalize_wos_query(query: str | None) -> str | None:
    if not query:
        return query

    tags = '|'.join(SUPPORTED_FIELD_TAGS)
    return re.sub(rf'\b({tags})\s*=\s*', r'\1=', query.strip())


def build_wos_query(query: str, start_year: int | None = None, end_year: int | None = None) -> str:
    return normalize_wos_query(query) or ""


def build_publish_time_span(start_year: int | None = None, end_year: int | None = None) -> str | None:
    if not start_year and not end_year:
        return None

    if not start_year:
        start_year = 1900
    if not end_year:
        end_year = datetime.now().year

    today = date.today()
    end_date = date(end_year, 12, 31)
    if end_date > today:
        end_date = today

    return f"{start_year}-01-01+{end_date.isoformat()}"

def get_keywords_from_record(record: dict[str, Any]) -> list[str]:
    keywords = record.get('keywords', [])
    if isinstance(keywords, list):
        return [kw.get('keyword') for kw in keywords if 'keyword' in kw]
    return []


def get_author_display_name(author: Any) -> str | None:
    if isinstance(author, str):
        return author

    if not isinstance(author, dict):
        return None

    display_name = (
        author.get('displayName')
        or author.get('display_name')
        or author.get('fullName')
        or author.get('full_name')
        or author.get('name')
        or author.get('authorName')
    )
    if display_name:
        return display_name

    first_name = author.get('firstName') or author.get('first_name') or author.get('givenName')
    last_name = author.get('lastName') or author.get('last_name') or author.get('surname')
    return ' '.join(part for part in [first_name, last_name] if part) or None


def get_author_sequence(author: Any) -> int | None:
    if not isinstance(author, dict):
        return None

    seq = author.get('sequence') or author.get('seq') or author.get('order') or author.get('position')
    try:
        return int(seq)
    except (TypeError, ValueError):
        return None


def get_authors(record: dict[str, Any]) -> list[str]:
    authors = (
        get_value(lambda: record.get('names', {}).get('authors'))
        or get_value(lambda: record.get('authors', {}).get('authors', []))
    )
    if isinstance(authors, dict):
        authors = [authors]
    if not isinstance(authors, list):
        return []

    if authors and all(get_author_sequence(author) is not None for author in authors):
        authors = sorted(authors, key=get_author_sequence)

    return [
        display_name
        for author in authors
        if (display_name := get_author_display_name(author))
    ]



class WoSAPI(AbstractAPI):
    def __init__(self,
                 api_key: str,
                 # Number of records to return, must be 0-100.
                 page_size: int = 100,
                 # Database to search. WOK represents all databases.
                 # Available values : WOS, BCI, BIOABS, BIOSIS, CABI, CCC, CSCD, DCI, DIIDW, FSTA, GRANTS, INSPEC, MEDLINE, PPRN, PQDT, SCIELO, WOK, ZOOREC
                 database: str = 'WOK',
                 proxy: str | None = None,
                 max_req_per_sec: int = 5,
                 max_retries: int = 5,
                 backoff_rate: float = 5.,
               ):
        super().__init__(api_key=api_key, proxy=proxy, max_retries=max_retries,
                         max_req_per_sec=max_req_per_sec, backoff_rate=backoff_rate)
        self.database = database
        self.page_size = page_size

    def fetch_raw(self, query: str, start_year:int|None, end_year:int|None) -> Generator[dict[str, Any], None, None]:
   
        combined_query = build_wos_query(query, start_year, end_year)
        publish_time_span = build_publish_time_span(start_year, end_year)

        with RequestClient(
            backoff_rate=self.backoff_rate,
            max_req_per_sec=self.max_req_per_sec,
            max_retries=self.max_retries,
            proxy=self.proxy,
            timeout=30.0  # Set timeout to avoid ReadTimeouts
        ) as request_client:

            base_url = 'https://api.clarivate.com/apis/wos-starter/v1/documents'
            current_page = 1
            total_fetched = 0
            max_limit = 50  # WOS Starter API allows max 50 records per request
            page_size = min(self.page_size, max_limit)

            while True:
                self.logger.info(f'Fetching page {current_page}...')

                try:
                    params = {
                        'q': combined_query,           # e.g., TS="school uniform"
                        'db': "WOS",          # Web of Science Core Collection
                        'limit': page_size,
                        'page': current_page,
                    }
                    if publish_time_span:
                        params['publishTimeSpan'] = publish_time_span

                    response = request_client.get(
                        base_url,
                        params=params,
                        headers={
                            'X-ApiKey': self.api_key,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()

                    records = data.get('hits', [])
                    metadata = data.get('metadata', {})

                    if not records:
                        self.logger.info("No more records returned.")
                        break

                    yield from records
                    total_fetched += len(records)

                    if current_page==1 and metadata:
                        total_records = metadata['total']
                        self.logger.info(f"Total records in Web of Science is {total_records}.")
                    
                    self.logger.info(f"Fetched {total_fetched} records so far.")

                    # If we received fewer than requested, this is the last page
                    if len(records) < page_size:
                        self.logger.info("Last page reached.")
                        break

                    current_page += 1

                except Exception as e:
                    if hasattr(e, 'response') and e.response is not None:
                        self.logger.error(f"HTTP {e.response.status_code}: {e.response.text}")
                    else:
                        self.logger.error(str(e))
                    self.logger.exception("Exception while fetching WOS data")
                    raise


    @classmethod
    def translate_record(cls, record: dict[str, Any]) -> AcademicPaperData:
        ac= AcademicPaperData(
            item_id=uuid.uuid4(),
            doi=get_value(lambda: record.get('identifiers', {}).get('doi')),
            title=record.get('title'),
            wos_id=record.get('uid'),
            text=record.get('abstract'),
            publication_year=get_value(lambda: record.get('source', {}).get('publishYear')),
            source=get_value(lambda: record.get('source', {}).get('sourceTitle')),
            keywords=get_value(lambda: record.get('keywords', {}).get('authorKeywords')),
            authors=get_authors(record),
            meta={'wos-starter-api': record}
        )
        return ac
