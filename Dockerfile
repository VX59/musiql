FROM ubuntu:22.04

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    build-essential \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    libjpeg-dev \
    zlib1g-dev \
    && pip install --upgrade pip


RUN mkdir -p musiql
ADD pyproject.toml /musiql/pyproject.toml
ADD musiql /musiql/musiql
ADD musiql_api /musiql/musiql_api

WORKDIR /musiql

RUN pip install uv

RUN uv sync

ADD .env /musiql/.env
ADD musiql-desktop /musiql/musiql-desktop
ADD recommendation-models /musiql/recommendation-models

CMD ["uv", "run", "musiql", "--host", "0.0.0.0", "--port", "8000"]