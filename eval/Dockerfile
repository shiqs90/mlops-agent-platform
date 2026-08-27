FROM python:3.12-slim

WORKDIR /app

# Install from the LOCK, not requirements.txt — see nova/Dockerfile for why.
# Regenerate after changing requirements.txt:
#   uv pip compile eval/requirements.txt -o eval/requirements.lock \
#     --python-platform linux --python-version 3.12
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# The fixture ships INSIDE the image, not mounted from a ConfigMap. The questions and
# the scoring logic are versioned together — an image tag then identifies one exact
# suite, which is what makes two runs comparable.
COPY run_eval.py .
COPY golden/ golden/

RUN useradd --create-home --uid 10001 evalrunner
USER 10001

# No CMD with a target: the Job supplies --target, so the same image scores the live
# service or a rollout's preview service without a rebuild.
ENTRYPOINT ["python", "run_eval.py"]
