FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser app.py instagram.py start.py ./
COPY --chown=appuser:appuser web ./web
USER appuser
EXPOSE 8000
CMD ["python", "start.py"]
