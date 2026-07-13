from typing import  Iterable, Any, List
from django.utils.text import slugify
from datetime import date
from django.db.models import Q
from PB_Assistant.models import (
    AcademicPaper,
    PlanetaryBoundary,
)
from PB_Assistant.apps.ingestion.sources.data_models import  AcademicPaperData
import logging
logger = logging.getLogger(__name__)

IDENTIFIERS= ['doi', 'wos_id', 'scopus_id', 'openalex_id']
SOURCE_IDENTIFIERS = ['wos_id', 'scopus_id', 'openalex_id']


def generate_title_slug(title: str | None) -> str | None:
    return slugify(title) if title else None


def find_duplicate(item: AcademicPaperData) -> AcademicPaper | None:
    if item.doi:
        match = AcademicPaper.objects.filter(doi=item.doi).first()
        if match:
            return match

    if item.title and item.publication_year:
        match = AcademicPaper.objects.filter(
            title_slug=slugify(item.title),
            publication_year=item.publication_year,
        ).first()
        if match:
            return match

    # Source IDs only identify duplicates within the same source.
    filters = Q()
    for field in SOURCE_IDENTIFIERS:
        value = getattr(item, field, None)
        if value:
            filters |= Q(**{field: value})

    # Run a single query if any filters are defined
    if filters:
        match = AcademicPaper.objects.filter(filters).first()
        if match:
            return match

    # Preserve the old fallback for records without reliable identifiers/year.
    if item.title:
        match = AcademicPaper.objects.filter(title_slug=slugify(item.title)).first()
        if match:
            return match

    return None


def merge_missing_item_data(existing: AcademicPaper, data: AcademicPaperData) -> bool:
    changed_fields = []

    for field in IDENTIFIERS:
        incoming_value = getattr(data, field, None)
        if incoming_value and not getattr(existing, field):
            setattr(existing, field, incoming_value)
            changed_fields.append(field)

    merge_fields = [
        'title',
        'title_slug',
        'text',
        'publication_year',
        'source',
        'best_oa_pdf_url',
        'keywords',
        'meta',
    ]
    for field in merge_fields:
        incoming_value = getattr(data, field, None)
        if incoming_value and not getattr(existing, field):
            setattr(existing, field, incoming_value)
            changed_fields.append(field)

    if data.all_pdf_urls and not existing.all_pdf_urls:
        existing.all_pdf_urls = data.all_pdf_urls
        changed_fields.append('all_pdf_urls')

    if data.authors and not existing.author_list:
        existing.author_list = data.authors
        changed_fields.append('author_list')

    if changed_fields:
        existing.save(update_fields=changed_fields)
        return True

    return False


def insert_new_item(data: AcademicPaperData) -> AcademicPaper:
    title = safe_truncate(data.title, length=512)
    title_slug = safe_truncate(data.title_slug or (slugify(title) if title else None), length=512)
    # Step 1: Create base AcademicItem (excluding ManyToMany)
    item = AcademicPaper(
        paper_id=data.item_id,
        doi=data.doi,
        time_edited=data.time_edited,
        text=data.text,
        wos_id=data.wos_id,
        scopus_id=data.scopus_id,
        openalex_id=data.openalex_id,
        title=title,
        title_slug=title_slug,
        best_oa_pdf_url= data.best_oa_pdf_url,
        all_pdf_urls=data.all_pdf_urls,
        publication_year=data.publication_year,
        source=data.source,
        keywords=data.keywords,
        meta=data.meta,
        author_list=data.authors,
    )
    item.save()

    return item

def safe_truncate(value, length=255):
    return value[:length] if value else ''

def add_planetary_boundary(item: AcademicPaper, pb_input):
    """
    Safely add one or more PlanetaryBoundary objects to an AcademicItem
    without overwriting existing ones. Accepts a single object.
    """
    if not pb_input:
        return

    # Add only new ones
    existing_ids = set(item.planetary_boundary.values_list("id", flat=True))
    if pb_input.id not in existing_ids:
        item.planetary_boundary.add(pb_input)

def import_academic_paper(item: AcademicPaperData, planetary_boundary: PlanetaryBoundary):
    status=''
    if not any(getattr(item, field) for field in IDENTIFIERS) and not getattr(item, 'title', None):  # skip record if no identifier or title provided where we can check duplicates
        status='skipped_empty'
        return status, None

    item.title_slug = generate_title_slug(item.title)

    existing = find_duplicate(item)

    if not existing:
        obj = insert_new_item(item)
        add_planetary_boundary(obj, planetary_boundary)
        status='new_record'
        academicpaper=obj
    else:
        merge_missing_item_data(existing, item)
        add_planetary_boundary(existing,
                               planetary_boundary)  # adds a new non-existent planetary boundary relationship to an existing record
        academicpaper=existing
        status = 'skipped_duplicate'
    return status, academicpaper

def import_academic_papers(
    new_items: Iterable[AcademicPaperData], planetary_boundary: PlanetaryBoundary,
) -> dict:
    """
    importer that deduplicates AcademicPapers
    """
    new_count = 0
    skipped_duplicate_count = 0
    skipped_empty_count = 0

    for item in new_items:
        status, item = import_academic_paper(item, planetary_boundary)
        if status == 'skipped_empty':
            skipped_empty_count+=1
        elif status == 'new_record':
            new_count+=1
        elif status == 'skipped_duplicate':
            skipped_duplicate_count+=1

    return {
        "empty_records_skipped": skipped_empty_count,
        "new_items": new_count,
        "duplicates_skipped": skipped_duplicate_count,
    }
