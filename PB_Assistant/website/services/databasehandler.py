from django.forms.models import model_to_dict
from PB_Assistant.models import SearchHistory, AcademicPaper, AcademicPaperText
import logging
logger = logging.getLogger(__name__)


def format_author_name(author):
    if isinstance(author, str):
        return author
    if isinstance(author, dict):
        return (
            author.get("name")
            or author.get("display_name")
            or author.get("displayName")
            or author.get("authname")
            or ""
        )
    return ""


def format_authors(author_list):
    return ", ".join(
        name
        for author in author_list or []
        if (name := format_author_name(author))
    )


class DatabaseHandler:

    def retrieve_articles_by_doc_ids(self, doc_ids):
        try:
            academicpapers = AcademicPaper.objects.filter(academicpaper_text__id__in=doc_ids).distinct()
            results = []
            for academicpaper in academicpapers:
                article_dict = model_to_dict(academicpaper)
                article_dict['academicpaper_text_id'] = academicpaper.academicpaper_text.id if academicpaper.academicpaper_text else None
                article_dict['authors_string'] = format_authors(academicpaper.author_list)
                results.append(article_dict)
            return results
        except Exception as e:
            logger.error(f"Error fetching articles: {e}")
            return []

    def save_search_history(self, user_id, query, answer, chunk_ids, serialized_docs):
        try:
            history = SearchHistory.objects.create(
                user_id=user_id,
                query=query,
                answer=answer,
                source_documents=serialized_docs,
                chunk_ids=chunk_ids
            )
            logger.info("Search history saved successfully.")
            return history.id
        except Exception as e:
            logger.error(f"Error saving search history: {e}")
            raise

    def retrieve_search_history_by_user(self, user_id):
        try:
            return list(
                SearchHistory.objects
                .filter(user_id=user_id)
                .order_by('-timestamp')
                .values('id', 'query', 'timestamp')
            )
        except Exception as e:
            logger.error(f"Error retrieving search history: {e}")
            return []

    def retrieve_search_history_item(self, history_id):
        try:
            history = SearchHistory.objects.filter(pk=history_id).first()
            return model_to_dict(history) if history else None
        except Exception as e:
            logger.error(f"Error retrieving search history item: {e}")
            return None

    def delete_search_history_item(self, history_id):
        try:
            deleted_count, _ = SearchHistory.objects.filter(pk=history_id).delete()
            if deleted_count > 0:
                logger.info(f"Deleted history item with ID {history_id}.")
        except Exception as e:
            logger.error(f"Error deleting history item: {e}")

    def get_academicitem_ids_from_text_ids(self, text_ids: list[int]) -> list[int]:
        mapping = dict(
            AcademicPaperText.objects
            .filter(id__in=text_ids)
            .values_list("id", "paper_id")
        )
        return [mapping[tid] for tid in text_ids if tid in mapping]


