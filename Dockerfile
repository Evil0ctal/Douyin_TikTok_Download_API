# Use the official Ubuntu base image
FROM ubuntu:jammy
LABEL maintainer="Evil0ctal"

# Set non-interactive frontend (useful for Docker builds)
ENV DEBIAN_FRONTEND=noninteractive

# Update the package list and install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3-pip \
    python3.11-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set a working directory
WORKDIR /app

# Copy the application source code to the container
COPY . /app

# Install pip and set the PyPI mirror (Aliyun)
RUN pip3 install -i https://mirrors.aliyun.com/pypi/simple/ -U pip \
    && pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# Install dependencies directly
RUN pip3 install --no-cache-dir -r requirements.txt

# Make the start script executable
RUN chmod +x start.sh

# Command to run on container start
CMD ["./start.sh"]
