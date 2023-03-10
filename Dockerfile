FROM ubuntu:jammy
MAINTAINER jwstar
ENV DEBIAN_FRONTEND=noninteractive

RUN  apt-get -y update  \
    && apt-get install -y --no-install-recommends \
     python3.11 python3-pip python3.11-dev nodejs

COPY . /app
WORKDIR /app
RUN pip3 --no-cache-dir install --user -r /app/requirements.txt


RUN chmod +x start.sh && \
    apt-get autoremove -y \
    && apt-get remove -y python3-pip

CMD ["./start.sh"]
