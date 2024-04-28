#!/bin/bash

# Set script to exit on any errors.
set -e

echo 'Updating package lists...'
sudo apt-get update

echo 'Installing Git...'
sudo apt-get install -y git

echo 'Installing Python3...'
sudo apt install -y python3

echo 'Installing PIP3...'
sudo apt install -y python3-pip

echo 'Installing python3-venv...'
sudo apt install -y python3-venv

echo 'Creating path: /www/wwwroot'
sudo mkdir -p /www/wwwroot

cd /www/wwwroot || { echo "Failed to change directory to /www/wwwroot"; exit 1; }

echo 'Cloning Douyin_TikTok_Download_API.git from Github!'
sudo git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git

cd Douyin_TikTok_Download_API/ || { echo "Failed to change directory to Douyin_TikTok_Download_API"; exit 1; }

echo 'Creating a virtual environment'
python3 -m venv venv

echo 'Activating the virtual environment'
source venv/bin/activate

echo 'Setting pip to use the default PyPI index'
pip config set global.index-url https://pypi.org/simple/

echo 'Installing dependencies from requirements.txt'
pip install -r requirements.txt

echo 'Deactivating the virtual environment'
deactivate

echo 'Adding Douyin_TikTok_Download_API to system service'
sudo cp daemon/* /etc/systemd/system/

echo 'Enabling Douyin_TikTok_Download_API service'
sudo systemctl enable Douyin_TikTok_Download_API.service

echo 'Starting Douyin_TikTok_Download_API service'
sudo systemctl start Douyin_TikTok_Download_API.service

echo 'Douyin_TikTok_Download_API installation complete!'
