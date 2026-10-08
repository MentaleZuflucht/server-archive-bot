FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

# Fixed UID 1000, so a bind-mounted archive folder owned by the usual first host user is writable.
RUN useradd --uid 1000 --no-create-home bot \
    && mkdir logs archive \
    && chown bot logs archive
USER bot

# docker logs has no TTY, so colors are forced on. Set NO_COLOR=1 to turn them off.
ENV FORCE_COLOR=1

CMD ["python", "bot.py"]
