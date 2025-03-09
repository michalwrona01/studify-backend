FROM debian:bullseye-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
ENV PATH="/usr/bin/python3:${PATH}"
WORKDIR /code
COPY req.txt /code/
RUN pip3 install --no-cache-dir -r req.txt
COPY . /code/