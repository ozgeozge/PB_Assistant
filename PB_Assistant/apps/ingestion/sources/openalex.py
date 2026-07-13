import uuid
from typing import Any, Generator
from pathlib import Path
from .abstractsource import AbstractAPI
from PB_Assistant.apps.ingestion.sources.data_models import (
    AcademicPaperData,

)
from .util import clear_empty, RequestClient
from datetime import datetime
from django.conf import settings
import os
import json


FIELDS_API = [
    'id',
    'doi',
    'title',
    'display_name',
    'publication_year',
    'publication_date',
    'ids',
    'language',
    'primary_location',
    'type',
    'type_crossref',
    'indexed_in',
    'open_access',
    'authorships',
    'apc_list',
    'apc_paid',
    'fwci',
    'has_fulltext',
    'fulltext_origin',
    'is_retracted',
    'is_paratext',
    'topics',
    'keywords',
    'locations',
    'best_oa_location',
    'datasets',
    'referenced_works',
    'abstract_inverted_index',
    'updated_date',
    'created_date',
]
#CURSOR_PATH = settings.BASE_DIR+"/cursors/openalex_last_cursor.txt"
CURSOR_PATH = os.path.join(settings.BASE_DIR, 'cursors/openalex_last_cursor.txt')


def build_year_filter(query: str | None, start_year: int | None, end_year: int | None) -> str | None:
    year_filter = None

    if start_year and end_year:
        year_filter = f"publication_year:{start_year}-{end_year}"
    elif start_year:
        current_year = datetime.now().year
        year_filter = f"publication_year:{start_year}-{current_year}"
    elif end_year:
        year_filter = f"publication_year:1900-{end_year}"  # unlikely but valid
    if query:
        query+=f",primary_topic.domain.id:3" #fetch articles in only Physical Sciences domain

    if query and year_filter:
        return f"{query},{year_filter}"
    elif year_filter:
        return year_filter
    else:
        return query  # may be None or original query


def get_author_display_names(authorships: Any) -> list[str]:
    if not authorships:
        return []

    if isinstance(authorships, str):
        try:
            authorships = json.loads(authorships)
        except json.JSONDecodeError:
            return []

    author_names = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue

        author = authorship.get("author") or {}
        if not isinstance(author, dict):
            continue

        display_name = author.get("display_name")
        if display_name:
            author_names.append(display_name)

    return author_names


class OpenAlexAPI(AbstractAPI):

    def fetch_raw(self, query: str, start_year:int|None, end_year:int|None) -> Generator[dict[str, Any], None, None]:
        cursor = '*'
        cursor_path = Path(CURSOR_PATH)
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        # Create file if it doesn't exist
        cursor_path.touch(exist_ok=True)

        try:
            with cursor_path.open("r") as f:
                cursor = f.read().strip() or "*"
        except FileNotFoundError:
            cursor = "*"
        n_pages = 0
        n_works = 0
        first_page=True
        combined_filter = build_year_filter(query, start_year, end_year)
        with RequestClient(backoff_rate=self.backoff_rate,
                           max_req_per_sec=self.max_req_per_sec,
                           max_retries=self.max_retries,
                           proxy=self.proxy) as request_client:
            while cursor is not None:
                n_pages += 1

                page = request_client.get(
                    'https://api.openalex.org/works',
                    params={
                        'filter': combined_filter,
                        'select': ','.join(FIELDS_API),
                        'cursor': cursor,
                        'per-page': 100
                    },
                
                ).json()
                cursor = page['meta']['next_cursor']
                if not cursor:
                    self._write_cursor_to_file("")
                else:
                    self._write_cursor_to_file(cursor)
                if first_page:
                    self.logger.info(f"Total records in Open Alex is  {page['meta']['count']}")
                    first_page=False
                self.logger.info(f"Retrieved {n_works:,} / {page['meta']['count']:,} | currently on page {n_pages:,}")

                yield from page['results']
                n_works += len(page['results'])
    
    def _write_cursor_to_file(self, cursor:str):
        with open(CURSOR_PATH, "w") as f:
            f.write(cursor)

    def _fetch_transformed(self, query, start_year, end_year):
        for record in self.fetch_raw(query, start_year, end_year):
            yield self._transform_record(record)

    def _transform_record(self, record: dict) -> dict:
       
        # Convert abstract_inverted_index into plain text abstract if available;
        # otherwise, fall back to the "abstract" field.
        ai_index = record.get("abstract_inverted_index")
        abstract_text = reconstruct_abstract(ai_index) if ai_index else record.get("abstract")

        # Build the title_abstract field by concatenating title and abstract_text (if present)
        title = record.get("title")
        title_abstract = None
        if title or abstract_text:
            title_abstract = f"{title or ''} {abstract_text or ''}".strip() or None

        locations = record.get("locations")
        if locations is not None and not isinstance(locations, str):
            locations = json.dumps(locations)

        authorships = record.get("authorships")
        if authorships is not None and not isinstance(authorships, str):
            authorships = json.dumps(authorships)

        work = {
            "id": record.get("id"),
            "display_name": record.get("display_name"),
            "title": title,
            "abstract": abstract_text,
            "title_abstract": title_abstract,
            "cited_by_count": record.get("cited_by_count"),
            "created_date": record.get("created_date"),
            "doi": record.get("doi"),
            "mag": record.get("mag"),
            "pmid": record.get("pmid"),
            "pmcid": record.get("pmcid"),
            # Using open_access field to determine is_oa if available
            "is_oa": record.get("open_access", {}).get("is_oa") if record.get("open_access") else None,
            "is_paratext": record.get("is_paratext"),
            "is_retracted": record.get("is_retracted"),
            "language": record.get("language"),
            "publication_date": record.get("publication_date"),
            "publication_year": record.get("publication_year"),
            "type": record.get("type"),
            "updated_date": record.get("updated_date"),
            "locations": locations,
            "authorships": authorships,
        }
        return work

    @classmethod
    def translate_record(cls, record: dict[str, Any]) -> AcademicPaperData:

        source = None
        record_locations = record["locations"]
        if record_locations:
            for record_location in record_locations:
                if record_location["source"] and record_location["source"]["display_name"]:
                    source = record_location["source"]["display_name"]
                    break

        doi = record["doi"].replace('https://doi.org/', '') if record["doi"] else None
        authors = get_author_display_names(record.get("authorships"))
        pubmed_id = record.get("ids").get("pmid") or None
        abstract = reconstruct_abstract(record.get("abstract_inverted_index"))
        item = AcademicPaperData(
            item_id=uuid.uuid4(),
            doi=doi,
            openalex_id=record["id"],
            title=record["title"],
            text=abstract,
            publication_year=record["publication_year"],
            source=source,
            authors=authors,
            best_oa_pdf_url = record.get("best_oa_location").get("pdf_url") if record.get("best_oa_location") else None,
            all_pdf_urls=[loc.get("pdf_url") for loc in record.get("locations") if loc.get("pdf_url")],
            meta=clear_empty({
                'openalex': {
                    'locations': record_locations,
                    'type': record["type"],
                    'updated_date': record["updated_date"],
                    'mag': record.get("ids").get("mag"),
                    'pmid': pubmed_id,
                    'pmcid': record.get("ids").get("pmcid"),
                    'display_name': record["display_name"],
                    'is_oa': record.get("is_oa"),
                    'is_paratext': record.get("is_paratext"),
                    'is_retracted': record.get("is_retracted"),
                    'language': record.get("language")
                }
            })
        )
        return item

def reconstruct_abstract(abstract_inverted_index=None):
    # If there's no abstract data, return an empty string.
    if not abstract_inverted_index:
        return None
    # Compute the maximum position from the available positions.
    max_index = max(pos for positions in abstract_inverted_index.values() for pos in positions)
    # Create a list of the proper length.
    abstract_list = [None] * (max_index + 1)
    for word, positions in abstract_inverted_index.items():
        for pos in positions:
            abstract_list[pos] = word
    # Join the words, filtering out any None values.
    return " ".join(word for word in abstract_list if word)
