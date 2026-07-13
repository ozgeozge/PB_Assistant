"""AbstractSource class

Inherit from this class to implement a new source for articles.
"""

from django.conf import settings
from typing import Any, Callable, Iterable, Generator, Annotated
from pathlib import Path
import json
import uuid
from abc import ABC, abstractmethod
import logging

from PB_Assistant.models import AcademicPaper, PlanetaryBoundary
from PB_Assistant.apps.ingestion.importer import import_academic_papers
from typing import List


class AbstractAPI(ABC):
    def __init__(self,
                 api_key: str,
                 proxy: str | None = None,
                 max_req_per_sec: int = 5,
                 max_retries: int = 5,
                 backoff_rate: float = 5.,
           ):
        self.api_key = api_key
        self.proxy = proxy
        self.logger=logging.getLogger(__name__)
        self.max_req_per_sec = max_req_per_sec
        self.max_retries = max_retries
        self.backoff_rate = backoff_rate

    @abstractmethod
    def fetch_raw(self, query: str, start_year:int|None, end_year:int|None) -> Generator[dict[str, Any], None, None]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def translate_record(cls, record: dict[str, Any]) -> AcademicPaper:
        raise NotImplementedError

    def fetch_translated(self, query: str, start_year:int=None, end_year:int=None) -> Generator[AcademicPaper, None, None]:
        for record in self.fetch_raw(query, start_year, end_year):
            yield self.translate_record(record)

    def download(self, pb:PlanetaryBoundary, query:str,  start_year:int=None, end_year:int=None):
        new_dict = import_academic_papers(new_items=self.fetch_translated(query=query, start_year=start_year, end_year=end_year), planetary_boundary=pb)
        self.logger.info(new_dict)

