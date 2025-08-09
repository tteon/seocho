# datahub/Dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    curl git build-essential && \
    pip install --upgrade pip && \
    pip install acryl-datahub

WORKDIR /app

COPY ./recipe_glossary.yml ./recipe_glossary.yml
COPY ./bootstrap.sh ./bootstrap.sh
RUN chmod +x ./bootstrap.sh

entrypoint ["/bin/bash", "-c", "./bootstrap.sh && tail -f /dev/null"]
