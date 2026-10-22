FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# 注意：运行时需要把 hermes-agent 挂载进来
# docker-compose 会 volume mount ~/.hermes/hermes-agent 到 /hermes-agent

COPY main.py .
ENV HERMES_PATH=/hermes-agent

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
