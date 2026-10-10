FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ztna_ml/ ztna_ml/
RUN useradd --system --uid 10002 ml
USER ml
CMD ["python", "-m", "ztna_ml.worker"]
