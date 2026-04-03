FROM public.ecr.aws/lambda/python:3.10

RUN yum install -y \
    gcc \
    gcc-gfortran \
    atlas-devel \
    lapack-devel \
    libjpeg-turbo-devel \
    zlib-devel

WORKDIR /var/task

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

COPY musiql ./musiql
COPY musiql_api ./musiql_api
COPY .env .
COPY musiql-desktop ./musiql-desktop
COPY recommendation-models ./recommendation-models

CMD ["musiql.handler.handler"]
