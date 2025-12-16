
import logging
import time
import requests
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.http import HttpResponseBadRequest, JsonResponse
from django.contrib import messages
from django.conf import settings
from django.shortcuts import render, redirect
from .services.databasehandler import DatabaseHandler
from .services.articlerenderer import ArticleRenderer
from .services.qa_chain import build_llm_chain, build_custom_retrieval_qa_chain,  process_qa_response, serialize_documents
from PB_Assistant.apps.textprocessing.embedder import TextEmbedder

logger = logging.getLogger(__name__)
db_handler = DatabaseHandler()
embedder = TextEmbedder()

OLLAMA_BASE_URL = settings.OLLAMA_BASE_URL
def ollama_models(request):
    """
    Returns: {"models": ["llama3:latest", "mistral:7b", ...]}
    """
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json() or {}
        names = sorted(
            {m.get("name") for m in data.get("models", []) if m.get("name")},
            key=str.lower
        )
        return JsonResponse({"models": list(names)})
    except requests.RequestException as e:
        # Friendly fallback for frontend; you can log e
        return JsonResponse({"models": [], "error": "Ollama unreachable"}, status=503)
@require_GET
def index(request):
    return render(request, 'website/index.html')

@require_POST
def search(request):
    user_query = (request.POST.get('user_prompt') or '').strip()
    selected_model = (request.POST.get('model') or '').strip()

    if not user_query:
        return HttpResponseBadRequest("user_prompt is required")

    # Enforce model selection (or relax this if you have a default)
    if not selected_model:
        messages.error(request, "Please select a model before running a search.")
        return redirect('website/index.html')  # or render the same page

    # Persist the last chosen model for convenience
    request.session['ollama_model'] = selected_model

    query_vector = embedder.embed_text(user_query)
    llm_chain = build_llm_chain(model_name=selected_model)
    qa = build_custom_retrieval_qa_chain(llm_chain, query_vector)

    start_time = time.time()
    response = qa(user_query)
    elapsed_time = time.time() - start_time
    logger.info(f"Time for inference: {elapsed_time:.03f} seconds")

    result = response.get('result', '')
    retrieved_documents = response.get('source_documents', [])

    answer, chunk_ids, doc_ids = process_qa_response(result, retrieved_documents)
    serialized_docs = serialize_documents(retrieved_documents)

    user_id = request.user.id if request.user.is_authenticated else 1
    # Save and fetch new history ID if needed
    db_handler.save_search_history(user_id, user_query, answer, chunk_ids, serialized_docs)

    retrieved_doc_ids = [doc.metadata['id'] for doc in retrieved_documents]
    articles = db_handler.retrieve_articles_by_doc_ids(retrieved_doc_ids)
    articles_as_dict = ArticleRenderer.render_articles_and_contents(
        articles, serialized_docs, chunk_ids
    )

    return render(request, 'website/search_result.html', {
        'query': user_query,
        'answer': answer,
        'articles': articles_as_dict,
    })

@require_GET
def history(request):
    history_records = db_handler.retrieve_search_history_by_user(user_id=request.user.id if request.user.is_authenticated else 1)

    user_prompt_history = [
        {"id": record["id"], "title": record["query"]}
        for record in history_records
    ] if history_records else []

    return JsonResponse(user_prompt_history, safe=False)

@require_GET
def load_history_item(request, id):
    history_item = db_handler.retrieve_search_history_item(history_id=id)
    if not history_item:
        return JsonResponse({'error': 'Not found'}, status=404)

    user_query = history_item['query']
    answer = history_item['answer']
    source_documents = history_item['source_documents']
    chunk_ids = history_item['chunk_ids']

    doc_ids = [doc['metadata']['id'] for doc in source_documents]
    articles = db_handler.retrieve_articles_by_doc_ids(doc_ids)
    articles_as_dict = ArticleRenderer.render_articles_and_contents(
        articles, source_documents, chunk_ids
    )

    return render(request, 'website/search_result.html', {
        'query': user_query,
        'answer': answer,
        'articles': articles_as_dict,
    })

@require_http_methods(['DELETE'])
def delete_history(request, id):
    db_handler.delete_search_history_item(history_id=id)
    return JsonResponse({}, status=204)
