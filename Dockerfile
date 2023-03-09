# Credits
# https://github.com/jw-star
FROM python:3.10.5-slim-buster
MAINTAINER evil0ctal (https://hub.docker.com/repository/docker/evil0ctal/douyin_tiktok_download_api/general)
RUN apt-get update
RUN apt-get -y install gcc
RUN apt-get -y install nodejs
RUN npm install -y md5
COPY . /app
RUN pip3 --no-cache-dir install --user -r /app/requirements.txt
WORKDIR /app
# -u print打印出来

RUN chmod +x start.sh

CMD ["./start.sh"]
