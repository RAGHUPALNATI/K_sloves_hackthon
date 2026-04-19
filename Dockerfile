FROM python:3.12-slim

# Note: Ollama must be running on the HOST machine, not inside the container.
# The container connects to it via OLLAMA_BASE_URL=http://host.docker.internal:11434

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /app/

# Expose port for FastAPI Webhook Server
EXPOSE 8000

# Default entrypoint runs the API webhook server
CMD ["python", "server.py"]
