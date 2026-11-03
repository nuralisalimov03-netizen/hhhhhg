FROM python:3.12-slim
WORKDIR /app
COPY server/app.py /app/app.py
COPY app/src/main/assets/web /app/web
ENV CHITS_WEB_DIR=/app/web
RUN mkdir -p /data && chown -R 10001:10001 /data /app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["python3", "/app/app.py"]
CMD ["serve", "--data-dir", "/data", "--host", "0.0.0.0", "--port", "8765"]
