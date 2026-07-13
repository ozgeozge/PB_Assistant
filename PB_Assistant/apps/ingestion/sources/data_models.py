from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
import uuid

@dataclass
class PlanetaryBoundaryData:
    name: str
    short_name: str
    search_query: str
    search_query_open_alex: Optional[str] = None
    search_query_scopus: Optional[str] = None
    search_query_wos: Optional[str] = None


@dataclass
class AcademicPaperData:
    item_id: uuid.UUID = field(default_factory=uuid.uuid4)
    doi: Optional[str] = None
    time_edited: Optional[datetime] =  None # ISO date string or datetime
    text: Optional[str] = None

    wos_id: Optional[str] = None
    scopus_id: Optional[str] = None
    openalex_id: Optional[str] = None

    title: Optional[str] = None
    title_slug: Optional[str] = None

    best_oa_pdf_url: Optional[str] = None
    all_pdf_urls: List[str] = field(default_factory=list)

    publication_year: Optional[int] = None
    source: Optional[str] = None

    keywords: Optional[Dict] = None
    authors: List[str] = field(default_factory=list)
    meta: Optional[Dict] = None

    planetary_boundary: List[PlanetaryBoundaryData] = field(default_factory=list)
