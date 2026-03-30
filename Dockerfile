FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
COPY data/ data/
RUN pip install --no-cache-dir .
ENV MEMOREEI_DB_PATH=/data/memoreei.db
VOLUME /data
ENTRYPOINT ["memoreei"]
CMD ["serve"]
