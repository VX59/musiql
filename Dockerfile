FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY musiql-desktop/package.json musiql-desktop/package-lock.json ./
RUN npm ci --ignore-scripts
COPY musiql-desktop/ ./
RUN npm run build

FROM public.ecr.aws/lambda/python:3.10
WORKDIR /var/task
ENV PYTHONPATH=/var/task
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
COPY boto3_tools.py ./boto3_tools.py
COPY utility.py ./utility.py
COPY settings.py ./settings.py
COPY authtoken_api.py ./authtoken_api.py

COPY database ./database
COPY musiql ./musiql
COPY musiql_api ./musiql_api
COPY --from=frontend-build /app/dist ./musiql-desktop/dist
CMD ["musiql.handler.handler"]
