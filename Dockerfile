FROM ubuntu:latest
MAINTAINER Xiaopang

WORKDIR /app

COPY . /app

RUN apt-get update \
  && apt-get install -y python3-pip python3 git \
  && cd /usr/local/bin \
  && ln -s /usr/bin/python3 python \
  && pip3 install -r /app/requirements.txt

CMD ["python" , "TikTok_ZH.py"]
