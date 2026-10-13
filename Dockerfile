FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRAFICA_DATA_DIR=/data
WORKDIR /app
COPY app.py security.py ./
COPY static/ ./static/
EXPOSE 8000
CMD ["python", "app.py"]
