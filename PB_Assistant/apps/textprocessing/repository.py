# PB_Assistant/apps/textprocessing/repository.py
from __future__ import annotations
from typing import Sequence, Dict, Optional, List
from django.db import transaction
from django.db.models import Q, QuerySet

from PB_Assistant.models import AcademicPaper, AcademicPaperText

class AcademicRepository:
    """
    Unified repository for selecting AcademicPaper rows needing text and
    writing AcademicPaperText in bulk.
    """

    # -------- SELECTORS

    def select_items_needing_text(
        self,
        *,
        pb_names: Sequence[str] | None,
        start_year: int,
        end_year: int,
        include_hasfulltext_false: bool = True,
    ) -> QuerySet:
        """
        Flexible selector used by both processors.

        require_pdf_urls:
          - True  -> only items that have at least one PDF URL
          - False -> only items with NO pdf urls
          - None  -> ignore this filter

        include_hasfulltext_false:
          - If True, include items that already have AcademicItemText but hasfulltext=False
        """
        pb_filter = (
            Q(academicpaperplanetaryboundary__planetary_boundary__short_name__in=pb_names)
            if pb_names else Q()
        )

        # Base: either no text yet, or text exists but marked not full
        text_filter = Q(academicpaper_text__isnull=True)
        if include_hasfulltext_false:
            text_filter |= Q(academicpaper_text__hasfulltext=False)

        qs = (
            AcademicPaper.objects
            .filter(text_filter)
            .filter(publication_year__range=(start_year, end_year))
            .filter(pb_filter)
        )
    

        return qs.distinct()

    def select_items_for_text_embedding(
            self, *, pb_names: Sequence[str] | None, start_year: int, end_year: int, exclude_abstract: bool = False
    ) -> QuerySet:
        pb_filter = Q(academicpaperplanetaryboundary__planetary_boundary__short_name__in=pb_names) if pb_names else Q()

        qs = (
            AcademicPaper.objects
            .filter(
                publication_year__range=(start_year, end_year),
                academicpaper_text__isnull=False,  # has the OneToOne text
                academicpaper_text__academicpaper_embeddings__isnull=True,  # no embeddings yet
            )
            .filter(pb_filter)
            .select_related("academicpaper_text")  # cheap join thanks to OneToOne
        )
        if exclude_abstract:
            qs = qs.filter(academicpaper_text__hasfulltext=True)
        return qs
    # Convenience wrappers (optional) to match previous names/behavior:

    def select_items_with_pdfs_needing_text(
        self, *, pb_names: Sequence[str] | None, start_year: int, end_year: int
    ) -> QuerySet:
        return self.select_items_needing_text(
            pb_names=pb_names, start_year=start_year, end_year=end_year,
            require_pdf_urls=True
        )

    # -------- WRITES

    def bulk_upsert_texts(self, texts_by_item: Dict[int, str], *, has_fulltext: bool) -> List:
        """
        Upsert AcademicPaperText for many items at once. Returns saved rows.
        """
        if not texts_by_item:
            return []
        records = [
            AcademicPaperText(academicpaper_id=iid, text=txt, hasfulltext=has_fulltext)
            for iid, txt in texts_by_item.items()
        ]
        with transaction.atomic():
            AcademicPaperText.objects.bulk_create(
                records,
                update_conflicts=True,
                update_fields=["text", "hasfulltext"],
                unique_fields=["academicpaper"],
            )
        return list(AcademicPaperText.objects.filter(academicpaper_id__in=texts_by_item.keys()))

    def save_abstracts(self, items: List, *, has_fulltext: bool) -> List:
        """
        Save abstracts as AcademicPaperText and return saved rows.
        """
        records = [
            AcademicPaperText(academicpaper=i, text=i.text, hasfulltext=has_fulltext)
            for i in items if getattr(i, "text", None)
        ]
        if not records:
            return []
        # de-dup within batch
        dedup = {}
        for r in records:
            dedup[r.academicpaper_id] = r
        with transaction.atomic():
            AcademicPaperText.objects.bulk_create(
                dedup.values(),
                update_conflicts=True,
                update_fields=["text", "hasfulltext"],
                unique_fields=["academicpaper"],
            )
        return list(AcademicPaperText.objects.filter(academicpaper_id__in=dedup.keys()))
