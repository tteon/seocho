FROM python:3.11-slim

WORKDIR /app

RUN pip install streamlit requests

COPY . .

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
