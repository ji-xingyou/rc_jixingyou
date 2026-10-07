FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV NOTIFY_DB_PATH=/data/notifications.db
VOLUME ["/data"]
EXPOSE 8080
CMD ["notification-service", "all"]

