
# =============================
# File: PB_Assistant/apps/textprocessing/pdf_processor.py
# =============================
from __future__ import annotations
import logging
import time
from typing import Dict, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .repository import AcademicRepository
from .pdf_downloader import PdfDownloader
from .pdf_text_extractor import PdfTextExtractor
from PB_Assistant.apps.textprocessing.embedder import TextEmbedder

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class PdfProcessorConfig:
    pb_names: Sequence[str] | None = None
    batch_size: int = 200
    max_papers: Optional[int] = None
    extract_workers: int = 8
    embed_workers: int = 4


logger = logging.getLogger(__name__)


class PdfProcessor:
    """Pipeline: select → download → extract → save → embed.
    - Parallelize per item in batches
    - Ensure at-most-one PDF per item
    - Use bulk upserts for AcademicPaperText
    """

    def __init__(
            self,
            pb_names,  # <- old style
            batch_size: int = 50,  # <- old default
            max_papers: Optional[int] = None,
            no_embed: Optional[bool] = False,
            *,
            # new-style kwargs still supported if ever needed:
            extract_workers: int = 8,
            embed_workers: int = 4,
            repo: Optional[AcademicRepository] = None,
    ) -> None:
        # Build the internal config from old-style args
        self.config = PdfProcessorConfig(
            pb_names=pb_names,
            batch_size=batch_size,
            max_papers=max_papers,
            extract_workers=extract_workers,
            embed_workers=embed_workers,
        )
        self.no_embed = no_embed
        self.repo = repo or AcademicRepository()
        self.downloader = PdfDownloader()
        self.extractor = PdfTextExtractor()
        self.embedder = TextEmbedder()

    # ---- Public entrypoint
    def process_academic_papers(self, start_year: int, end_year: int) -> None:
        qs = self.repo.select_items_needing_text(
            pb_names=self.config.pb_names, start_year=start_year, end_year=end_year,
        )
        total_candidates = qs.count()
        logger.info(
            "Processing %s papers from %s to %s%s",
            total_candidates,
            start_year,
            end_year,
            f" for PBs: {','.join(self.config.pb_names)}" if self.config.pb_names else "",
        )

        t0 = time.time()
        total_downloaded, total_embedded_with_pdf, successful_ids, attempted_ids = self._process_items(qs)

        items_without_pdf = qs.filter(id__in=attempted_ids).exclude(id__in=successful_ids)
        total_embedded_without_pdf = self._process_items_without_pdfs(items_without_pdf)
        logger.info(
            "Total downloaded: %s, embedded from PDF: %s, embedded from abstracts: %s, elapsed: %.2fs for year %s",
            total_downloaded,
            total_embedded_with_pdf,
            total_embedded_without_pdf,
            time.time() - t0,
            start_year
        )

    def _process_items(self, queryset) -> Tuple[int, int, List[int], List[int]]:
        total = self.config.max_papers if self.config.max_papers else queryset.count()
        #logger.info("Processing %s items with available PDF URLs", total)

        total_downloaded = 0
        total_embedded = 0
        total_download_time = total_extract_time = total_save_time = total_embed_time = 0.0
        successful_ids = []
        attempted_ids = []
        batches = max(1, (total + self.config.batch_size - 1) // self.config.batch_size)
        for i in range(0, total, self.config.batch_size):
            batch = list(queryset[i : i + self.config.batch_size])
            attempted_ids.extend(item.id for item in batch)
            logger.info("Batch %s/%s (size=%s)", i // self.config.batch_size + 1, batches, len(batch))

            # 1) download (one per item)
            t0 = time.time()
            filenames_by_item = self._download_batch(batch)
            download_time = time.time() - t0
            total_download_time += download_time
            downloaded = sum(1 for fn in filenames_by_item.values() if fn)
            total_downloaded +=downloaded
            # 2) extract
            t1 = time.time()
            texts_by_item = self._extract_batch(filenames_by_item)
            extract_time = time.time() - t1
            total_extract_time+=extract_time

            # 3) save
            t2 = time.time()
            saved_texts = self.repo.bulk_upsert_texts(texts_by_item, has_fulltext=True)
            save_time = time.time() - t2
            total_save_time+=save_time
            academicpaper_ids = [t.academicpaper_id for t in saved_texts]
            successful_ids.extend(academicpaper_ids)
            # 4) embed
            t3 = time.time()
            if not self.no_embed:
                embedded = self._embed_texts(saved_texts)
                total_embedded +=embedded
            embed_time = time.time() - t3
            total_embed_time += embed_time

            # 5) cleanup
            self.downloader.delete([fn for fn in filenames_by_item.values() if fn])

            logger.info(
                "Downloaded %s items in %.2fs s., extracted in %.2fs s., saved in %.2fs s. , %s items embeded in %.2fs s.",
                downloaded,
                download_time,
                extract_time,
                save_time,
                embedded,
                embed_time,
            )


        logger.info(
            "Timing — %s total download: %.2fs, extract: %.2fs, save: %.2fs, %s total embed: %.2fs",
            total_downloaded,
            total_download_time,
            total_extract_time,
            total_save_time,
            total_embedded,
            total_embed_time,
        )
        return total_downloaded, total_embedded, successful_ids, attempted_ids

    # ---- Items without PDFs → save abstract then embed
    def _process_items_without_pdfs(self, queryset) -> int:
        total = queryset.count()
        logger.info("Processing %s items without PDFs (saving abstracts)", total)

        embedded_total = 0
        for i in range(0, total, self.config.batch_size):
            batch = list(queryset[i : i + self.config.batch_size])
            saved_texts = self.repo.save_abstracts(batch, has_fulltext=False)
            if not self.no_embed:
                embedded_total += self._embed_texts(saved_texts)
        return embedded_total


    def _download_batch(self, items: List) -> Dict[int, Optional[str]]:
        """
        Try each item's all_pdf_urls; if none works, try Crossref by DOI; if still
        none, check publisher and attempt Nature/Springer scrapes.
        """
        results: Dict[int, Optional[str]] = {}
        for item in items:

            results[item.id] = None
            filename = f"pdf_{item.id}.pdf"

            # 1) direct URLs first
            for url in (item.all_pdf_urls or []):
                try:
                    if self.downloader.download(url, filename):
                        results[item.id] = filename
                        logger.debug("Download from Link successful for item %s url %s", item.id, url)
                        break
                except Exception:
                    logger.exception("Direct download failed for item %s url %s", item.id, url)

            if results[item.id]:
                continue  # success


        return results

    # ---- Stage 2: Extract via GROBID (parallel)
    def _extract_one(self, item_id: int, filename: str) -> Tuple[int, str]:
        try:
            text = self.extractor.extract_fulltext(filename=filename)
        except Exception:
            logger.exception("Extract failed for %s", filename)
            text = None
        return item_id, (text or "")

    def _extract_batch(self, filenames_by_item: Dict[int, Optional[str]]) -> Dict[int, str]:
        results: Dict[int, str] = {}
        tasks = [(iid, fn) for iid, fn in filenames_by_item.items() if fn]
        if not tasks:
            return results
        with ThreadPoolExecutor(max_workers=max(1, self.config.extract_workers)) as ex:
            futures = {ex.submit(self._extract_one, iid, fn): iid for iid, fn in tasks}
            for fut in as_completed(futures):
                iid, text = fut.result()
                if text:
                    results[iid] = text
        return results

    # ---- Stage 3: Embed (parallel, conservative)
    def _embed_one(self, ait) -> int:
        try:
            return 1 if self.embedder.embed_academic_paper(ait) else 0
        except Exception:
            logger.exception("Embed failed for AIT %s", getattr(ait, "id", None))
            return 0

    def _embed_texts(self, texts: list) -> int:
        if not texts:
            return 0
        total = 0
        with ThreadPoolExecutor(max_workers=max(1, self.config.embed_workers)) as ex:
            futures = [ex.submit(self._embed_one, t) for t in texts]
            for fut in as_completed(futures):
                total += fut.result()
        return total
