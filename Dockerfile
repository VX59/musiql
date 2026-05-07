FROM public.ecr.aws/lambda/python:3.10
WORKDIR /var/task
ENV PYTHONPATH=/var/task
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
COPY boto3_tools.py ./boto3_tools.py
COPY database ./database
COPY settings.py ./settings.py
COPY musiql ./musiql
COPY musiql_api ./musiql_api
CMD ["musiql.handler.handler"]