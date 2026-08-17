# One image, three servers. The entrypoint is chosen per Deployment, so a change to
# any connector is one build and three rollouts rather than three builds.
FROM python:3.12-slim

WORKDIR /app

# Install from the LOCK, not requirements.txt — the exact resolved tree including
# transitives. See nova/Dockerfile for why; the short version is that three outages
# in this build came from dependency resolution rather than code.
#
# Regenerate after changing requirements.txt:
#   uv pip compile mcp-servers/requirements.txt -o mcp-servers/requirements.lock \
#     --python-platform linux --python-version 3.12
#
# Dependencies before source: this layer only rebuilds when the lock changes, so
# iterating on a tool description is a sub-second build.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY common.py accounts.py transactions.py products.py ./

# Non-root. GKE doesn't enforce it by default, but a connector holding database
# credentials is exactly the workload where it matters.
RUN useradd --create-home --uid 10001 mcp
USER 10001

EXPOSE 8080

# Overridden per Deployment: accounts.py / transactions.py / products.py
CMD ["python", "accounts.py"]
