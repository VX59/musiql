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

# Pre-install numpy using binary wheel to avoid compiling
RUN pip install uv
RUN mkdir -p musiql
WORKDIR /musiql

ADD musiql-desktop /musiql/musiql-desktop
ADD pyproject.toml /pyproject.toml
RUN pip install \
    aioconsole>=0.8.2 \
    asyncpg>=0.31.0 \
    fastapi>=0.135.2 \
    ffmpeg>=1.4 \
    ffprobe>=0.5 \
    joblib>=1.5.3 \
    matplotlib>=3.10.8 \
    networkx>=3.4.2 \
    numpy==2.2.6 \
    pydantic-settings>=2.13.1 \
    sqlalchemy>=2.0.48 \
    tqdm>=4.67.3 \
    uvicorn>=0.42.0 \
    yt-dlp>=2026.3.17

ADD .env /musiql/.env
ADD musiql /musiql/musiql
ADD musiql_api /musiql/musiql_api
ADD recommendation-models /musiql/recommendation-models

CMD ["uvicorn", "musiql.server:main", "--host", "0.0.0.0", "--port", "8000", "--reload"]