FROM public.ecr.aws/lambda/python:3.10

WORKDIR /var/task

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY s3_service.py ./s3_service.py
COPY settings.py ./settings.py
COPY musiql ./musiql
COPY musiql_api ./musiql_api
COPY musiql-desktop ./musiql-desktop
COPY recommendation-models ./recommendation-models

CMD ["musiql.handler.handler"]
