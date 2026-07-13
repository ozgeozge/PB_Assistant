# PB Assistant --- Local Development Setup Guide

This guide provides a complete walkthrough for installing, configuring,
and running the PB Assistant application on your local machine. Follow
each section in order to fully set up the backend, vector database,
Grobid parser, and LLM environment.

# 1. Prerequisites

Before starting, ensure you have the following installed:

-   Python 3.10+
-   pip
-   Docker & Docker Compose
-   Git (optional, for cloning repositories)
-   RAM: At least 8GB (Ollama models may require more)
-   Disk space: At least 10GB free for models and PDFs

# 2. Project Setup

PB Assistant is a Django-based application. For a clean and reproducible
setup, we recommend using a Python virtual environment.

## 2.1 Create a Virtual Environment

From the project root:

    python -m venv .venv

Activate the environment:

    .venv\Scripts\activate

Verify that the environment is active:

    where python

Expected output should end with:

    .venv\Scripts\python

## 2.2 Install Python Dependencies

Inside the activated virtual environment:

    pip install -r requirements.txt

This installs all required libraries.

# 3. Docker Setup (PostgreSQL + Grobid + Ollama)

The PB Assistant system depends on:

-   PostgreSQL (with pgvector extension)
-   Grobid (for PDF text extraction)
-   Ollama (for local LLM inference)

These are all started using Docker Compose.

## 3.1 Create a .env File

Copy the example environment file and edit the values for your local
setup:

    copy .env.example .env

External API access requires user-provided credentials where applicable.
Users are responsible for complying with each provider's terms, rate
limits, and institutional license conditions. Do not commit API keys,
raw provider responses, downloaded PDFs, extracted text, embeddings, or
database dumps.

## 3.2 Start Docker Containers

Run:

    docker-compose up

This will start:

-   PostgreSQL database
-   Grobid server (for PDF structure extraction)
-   Ollama LLM environment

Allow up to 1-2 minutes for services to initialize.

## 3.3 Pull Ollama Models

Enter the Ollama container:

    docker exec -it ollama_container bash

Pull any required models:

    ollama pull model_name

Examples:

    ollama pull llama3
    ollama pull mistral

These models will power question answering.

# 4. Database Setup & pgvector Extension

PB Assistant uses pgvector inside PostgreSQL. You must enable the
extension using a Django migration. 

As this repository already includes initial migration files, you can directly continue to step [4.3](#43-apply-migrations).

## 4.1 Create a Migration for pgvector

    python manage.py makemigrations --empty PB_Assistant --name create_pgvector_extension

Edit the generated migration:

``` python
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = []

    operations = [
        migrations.RunSQL("CREATE EXTENSION IF NOT EXISTS vector;"),
    ]
```

## 4.2 Create Migrations

Now create other model-based migrations:

    python manage.py makemigrations

## 4.3 Apply migrations

    python manage.py migrate

# 5. Create an Admin User

Django includes an admin panel. Create a superuser:

    python manage.py createsuperuser

You can log in admin panel later at:

http://127.0.0.1:8000/admin/

# 6. Creating Initial Content

PB Assistant requires Planetary Boundaries to be created before
importing PDFs, fetching papers from external sources, or generating
embeddings.

## 6.1 Add a Planetary Boundary

Example:

    python manage.py shell -c "from PB_Assistant.models import PlanetaryBoundary; PlanetaryBoundary.objects.create(name='Climate Change', short_name='cc')"

Repeat this for each boundary you want to track (e.g., biodiversity,
land use, etc.). You can add them from admin panel after step 7 as well.

## 6.2 Configure External Sources

Before `fetch_papers` can run, the database must know two things:

-   which external APIs are available (`Source`)
-   which query to use for each planetary boundary and source
    (`BoundaryQuery`)

The `Source.name` values must match the API class names used in the
code: `OpenAlexAPI`, `ScopusAPI`, and `WoSAPI`.

First, insert the supported sources:

    psql -d your_db_name -f docs/setup_sources.sql

The SQL file inserts `OpenAlexAPI`, `ScopusAPI`, and `WoSAPI` into the
source table. It also contains example `BoundaryQuery` rows for the
Climate Change boundary with `short_name = 'cc'`. Edit `cc` and the
query strings in `docs/setup_sources.sql` before running it for other
planetary boundaries.

The queries must use the syntax expected by each source.

The fetcher uses `FETCHING_SOURCES` in `PB_Assistant/settings.py` to
decide which sources to query and in what order.

## 6.3 Fetch Papers From External Sources

Use `fetch_papers` to query configured external sources and insert
records into the `AcademicPaper` table:

    python manage.py fetch_papers --start-year 2025 --end-year 2026 --pb-names cc


If `--pb-names` is omitted, papers are fetched for all configured
planetary boundaries.

Deduplication is handled during import. Existing papers are matched by
DOI, then title plus publication year, then source-specific identifiers.
When a duplicate is found, missing identifiers and metadata are merged
into the existing `AcademicPaper` row.

## 6.4 Fetch and Process Full Text

After papers are inserted, use `fetch_fulltext` to process open-access
PDF URLs already stored on those records. The command tries to download
available PDFs, extract full text with Grobid, save `AcademicPaperText`,
and create embeddings unless disabled.

Example:

    python manage.py fetch_fulltext --start-year 2025 --end-year 2026 --pb-names cc

To skip embedding:

    python manage.py fetch_fulltext --start-year 2025 --end-year 2026 --pb-names cc --no-embed


If a PDF cannot be downloaded or extracted, the processor falls back to
storing the paper abstract when available.

## 6.5 Import Local PDFs

Import PDF files belonging to a particular boundary:

    python manage.py import_pdfs --folder folder_path --boundary short_name

Where:

-   `folder_path` = path containing PDF documents\
-   `short_name` = value from `PlanetaryBoundary.short_name` (e.g.,
    `cc`)

PB Assistant will store metadata, extract text using Grobid, and prepare
embeddings.

# 7. Start the Application

Run:

    python manage.py runserver 8000

Open:

http://127.0.0.1:8000/

You can now explore the app and query Planetary
Boundary knowledge using your locally running LLM.
