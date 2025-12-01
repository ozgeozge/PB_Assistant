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

Create a `.env` file next to your `docker-compose.yaml` containing:

    POSTGRES_DB=your_db_name
    POSTGRES_USER=your_username
    POSTPOSTGRES_PASSWORD=your_password
    POSTGRES_HOST=localhost
    POSTGRES_PORT=5432

    GROBID_URL=http://localhost:8070

    OLLAMA_BASE_URL=http://ollama:11434

This file will be read by both Django and Docker.

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

    docker exec -it ollama bash

Pull any required models:

    ollama pull model_name

Examples:

    ollama pull llama3
    ollama pull mistral

These models will power question answering.

# 4. Database Setup & pgvector Extension

PB Assistant uses pgvector inside PostgreSQL. You must enable the
extension using a Django migration.

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

Apply migration:

    python manage.py migrate

## 4.2 Apply Application Migrations

Now create and apply all other model-based migrations:

    python manage.py makemigrations
    python manage.py migrate

# 5. Create an Admin User

Django includes an admin panel. Create a superuser:

    python manage.py createsuperuser

You can log in admin panel later at:

http://127.0.0.1:8000/admin/

# 6. Creating Initial Content

PB Assistant requires Planetary Boundaries to be created before
importing PDFs or generating embeddings.

## 6.1 Add a Planetary Boundary

Example:

    python manage.py shell -c "from PB_Assistant.models import PlanetaryBoundary; PlanetaryBoundary.objects.create(name='Climate Change', short_name='cc')"

Repeat this for each boundary you want to track (e.g., biodiversity,
land use, etc.). You can add them from admin panel after step 7 as well.

## 6.2 Import PDFs

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
