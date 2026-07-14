FROM python:3.14.6-alpine
RUN mkdir -p /app
COPY app /app

RUN pip install -r app/requirements.txt

CMD [ "python3","app/main.py" ]