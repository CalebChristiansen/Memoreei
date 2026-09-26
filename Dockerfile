FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY data/ data/
RUN pip install --no-cache-dir .
# config.env and memoreei.db both live in /data: one volume to keep, one to back up.
ENV MEMOREEI_HOME=/data
# The dashboard answers on loopback only, and the container's loopback isn't the host's.
# Sign in with: docker compose run --rm memoreei admin-url
ENV MEMOREEI_ADMIN_REMOTE=true
VOLUME /data
EXPOSE 3679
ENTRYPOINT ["memoreei"]
CMD ["serve", "--http"]
