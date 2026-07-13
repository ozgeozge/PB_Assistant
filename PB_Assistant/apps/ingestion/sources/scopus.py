import uuid
from typing import Any, Generator
from httpx import codes, HTTPStatusError
from .abstractsource import AbstractAPI
from PB_Assistant.apps.ingestion.sources.data_models import (
    AcademicPaperData,
)
from .util import clear_empty, as_uuid, RequestClient, response_logger, get
from django.conf import settings
import os

#CURSOR_PATH = settings.BASE_DIR+"/cursors/scopus_last_cursor.txt"
CURSOR_PATH = os.path.join(settings.BASE_DIR, 'cursors/scopus_last_cursor.txt')
SCOPUS_STANDARD_PAGE_SIZE = 25
SCOPUS_OFFSET_RESULT_LIMIT = 5000


def get_title(obj: dict[str, Any]) -> str | None:
    return obj.get('dc:title')


def get_abstract(obj: dict[str, Any]) -> str | None:
    return obj.get('dc:description')


def get_author_display_name(author: dict[str, Any]) -> str | None:
    preferred_name = author.get('preferred-name')
    if not isinstance(preferred_name, dict):
        preferred_name = {}

    display_name = (
        author.get('authname')
        or author.get('ce:indexed-name')
        or preferred_name.get('ce:indexed-name')
    )
    if display_name:
        return display_name

    given_name = author.get('given-name') or author.get('ce:given-name') or preferred_name.get('ce:given-name')
    surname = author.get('surname') or author.get('ce:surname') or preferred_name.get('ce:surname')
    return ' '.join(part for part in [given_name, surname] if part) or None


def get_author_sequence(author: dict[str, Any]) -> int | None:
    seq = author.get('@seq') or author.get('seq')
    try:
        return int(seq)
    except (TypeError, ValueError):
        return None


def get_authors(obj: dict[str, Any]) -> list[str]:
    authors = obj.get('authors', {}).get('author', [])
    if isinstance(authors, dict):
        authors = [authors]
    if not isinstance(authors, list):
        return []

    author_records = [author for author in authors if isinstance(author, dict)]
    if all(get_author_sequence(author) is not None for author in author_records):
        author_records = sorted(author_records, key=get_author_sequence)

    return [
        display_name
        for author in author_records
        if (display_name := get_author_display_name(author))
    ]


def get_doi(obj: dict[str, Any]) -> str | None:
    return obj.get('prism:doi')


def get_id(obj: dict[str, Any]) -> str | None:
    return obj.get('eid', obj.get('dc:identifier'))


def get_py(obj: dict[str, Any]) -> int | None:
    py = obj.get('prism:coverDate')
    if py and len(py) >= 4:
        return int(py[:4])
    return None


def get_keywords(obj: dict[str, Any]) -> list[str] | None:
    kw = obj.get('authkeywords', '').split(' | ')
    return clear_empty(kw)


def parse_total_results(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_scopus_query(query: str, start_year: int | None = None, end_year: int | None = None) -> str:
    year_parts = []

    if start_year and end_year:
        year_parts.append(f"PUBYEAR > {start_year-1} AND PUBYEAR < {end_year+1}")
    elif start_year:
        year_parts.append(f"PUBYEAR > {start_year-1}")
    elif end_year:
        year_parts.append(f"PUBYEAR < {end_year+1}")

    if year_parts:
        if query:
            return f"({query}) AND {' AND '.join(year_parts)}"
        return ' AND '.join(year_parts)

    return query


def get_scopus_error_status_text(error: HTTPStatusError) -> str:
    try:
        data = error.response.json()
    except ValueError:
        return error.response.text

    return (
        get(data, 'service-error', 'status', 'statusText')
        or get(data, 'service-error', 'status', 'statusCode')
        or error.response.text
    )


class ScopusAPI(AbstractAPI):

    def fetch_raw(self, query: str, start_year:str|None, end_year:str|None) -> Generator[dict[str, Any], None, None]:
        """
        Scopus API wrapper for downloading all records for a given query.

        API overview
        https://dev.elsevier.com/

        API Documentation:
        https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl

        :param query:
        :return:
        """
        first_page=True
        with RequestClient(backoff_rate=self.backoff_rate,
                           max_req_per_sec=self.max_req_per_sec,
                           max_retries=self.max_retries,
                           proxy=self.proxy) as request_client:

            request_client.on(status=codes.UNAUTHORIZED, func=response_logger(self.logger))

            start = 0
            page_size = SCOPUS_STANDARD_PAGE_SIZE
            n_pages = 0
            n_records = 0
            while True:
                self.logger.info(f'Fetching page {n_pages}...')

                try:
                    page = request_client.get(
                        'https://api.elsevier.com/content/search/scopus',
                        params={
                            'query': build_scopus_query(query, start_year, end_year),
                            'start': start,
                            'count': page_size,
                            # https://dev.elsevier.com/sc_search_views.html
                            'view': 'STANDARD',
                        },
                        headers={
                            'Accept': 'application/json',
                            "X-ELS-APIKey": self.api_key,
                        },
                    )
                except HTTPStatusError as e:
                    status_text = get_scopus_error_status_text(e)
                    self.logger.error("Scopus API error: %s", status_text)
                    raise

                scopus_requests_limit = page.headers.get('x-ratelimit-limit')
                scopus_requests_remaining = page.headers.get('x-ratelimit-remaining')
                scopus_requests_reset = page.headers.get('x-ratelimit-reset')

                n_pages += 1
                data = page.json()

                entries = get(data, 'search-results', 'entry', default=[])
                n_results = parse_total_results(get(data, 'search-results', 'opensearch:totalResults', default=0))

                if first_page:
                    self.logger.info(f"Total records in Scopus is  {n_results}")
                    if n_results > SCOPUS_OFFSET_RESULT_LIMIT:
                        self.logger.warning(
                            "Scopus offset pagination is limited to the first %s records without cursor access.",
                            SCOPUS_OFFSET_RESULT_LIMIT,
                        )
                    first_page=False

                if len(entries) == 0 or n_results == 0:
                    break
                if len(entries) == 1 and entries[0].get('error') is not None:
                    break

                yield from entries

                n_records += len(entries)
                start += len(entries)
                self.logger.info(f'Found {n_records}/{n_results} records after processing page {n_pages} '
                                  f'(rate limit = {scopus_requests_limit} '
                                  f'| remaining = {scopus_requests_remaining} '
                                  f'| reset = {scopus_requests_reset})')

                if n_records >= n_results or start >= SCOPUS_OFFSET_RESULT_LIMIT:
                    break

    def _write_cursor_to_file(self, cursor:str):
        with open(CURSOR_PATH, "w") as f:
            f.write(cursor)

    @classmethod
    def translate_record(cls, record: dict[str, Any]) -> AcademicPaperData:
        return AcademicPaperData(
            item_id=uuid.uuid4(),
            doi=get_doi(record),
            title=get_title(record),
            scopus_id=get_id(record),
            text=get_abstract(record),
            publication_year=get_py(record),
            source=record.get('prism:publicationName'),
            keywords=get_keywords(record),
            authors=get_authors(record),
            meta={'scopus-api': clear_empty(record)}
        )
