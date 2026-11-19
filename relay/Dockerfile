FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "websockets>=13,<16"

COPY server.py .

ENV PORT=8899
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8899')+'/health',timeout=3)"

CMD ["python", "server.py", "--host", "0.0.0.0"]
