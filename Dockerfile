
FROM python:3.10.5-slim-buster
MAINTAINER jwstar (https://hub.docker.com/repository/docker/jwstar/douyin_tiktok_download_api)
RUN apt-get update && apt-get -y install gcc && apt-get -y install nodejs
COPY . /app
RUN pip3 --no-cache-dir install --user -r /app/requirements.txt
WORKDIR /app
# -u print打印出来

RUN chmod +x start.sh

CMD ["./start.sh"]
