# Backend image for Hugging Face Spaces (Docker SDK, free CPU tier). Built from the repo layout:
#   backend/  (API + agents + committed demo DB and search index)   data/docs/  (knowledge base)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/home/user
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 user          # Spaces run the container as uid 1000

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt \
      --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

COPY --chown=user backend backend
COPY --chown=user data/docs data/docs
RUN mkdir -p backend/models backend/.fastembed_cache && chown -R user /app

USER user
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
