from enum import Enum


class APIEnum(Enum):
    OA = 'OA'
    WOS = 'WOS'
    SCOPUS = 'SCOPUS'
    


def get_api_map():
    # Lazy import to avoid circular import during module initialization.
    from .openalex import OpenAlexAPI
    from .webofscience import WoSAPI
    from .scopus import ScopusAPI
    return {
        APIEnum.OA: OpenAlexAPI,
         APIEnum.WOS: WoSAPI,
         APIEnum.SCOPUS: ScopusAPI       
    }

__all__ = [
    "APIEnum",
    "get_api_map",
]
