FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
COPY app.py /app/app.py
USER nobody
EXPOSE 8080
CMD ["python", "/app/app.py"]
