FROM python:3.12-slim

WORKDIR /app

# Install from the LOCK, not requirements.txt.
#
# requirements.txt states intent (loose ranges); requirements.lock is the exact
# resolved tree, transitives included. Three outages in this build came from
# resolution rather than code — an unpinned `mcp` resolving too old, a pinned
# `langgraph-checkpoint-redis==0.1.*` resolving too old, and the API drift that
# followed. A lock makes a rebuild six months from now byte-identical instead of
# "whatever resolves that day".
#
# Regenerate after changing requirements.txt:
#   uv pip compile nova/requirements.txt -o nova/requirements.lock \
#     --python-platform linux --python-version 3.12
#
# --python-platform linux matters: resolving on macOS would pick macOS wheels for
# anything with native extensions.
#
# Dependencies before source: this layer only rebuilds when the lock changes, so
# iterating on the system prompt is a sub-second build rather than a two-minute one.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY app.py .

RUN useradd --create-home --uid 10001 nova
USER 10001

EXPOSE 8000

# One worker on purpose. The agent holds MCP sessions and a checkpointer connection
# opened at startup; multiple workers would each open their own set, multiplying
# connections against Postgres and Redis for no throughput gain at this traffic
# level. Scale with replicas, not workers.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
