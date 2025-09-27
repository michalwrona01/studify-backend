FROM python:3.13-slim
WORKDIR /code
COPY req.txt /code/
RUN pip install --no-cache-dir --upgrade -r /code/req.txt
COPY . /code/
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--access-log"]
