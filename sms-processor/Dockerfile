FROM python:3.12-slim

WORKDIR /app

COPY src/sms_processor/process.py ./process.py


CMD ["python", "process.py", "/input", "/output", "300"]
