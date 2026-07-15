FROM python:3.12-slim
WORKDIR /app
RUN useradd -r -u 1001 appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY loadgen.py .
USER appuser
CMD ["python", "-u", "loadgen.py"]
