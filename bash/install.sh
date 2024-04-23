#!/bin/bash

echo 'installing  Git...'

apt-get install git

echo 'installing  Python3...'

apt install python3

echo 'installing  PIP3...'

apt install python3-pip

echo 'Creating path: /www/wwwroot'

mkdir -p /www/wwwroot

cd /www/wwwroot || exit

echo 'Cloning Douyin_TikTok_Download_API.git from Github!'

git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git

cd Douyin_TikTok_Download_API/ || exit

sudo pip install -r requirements.txt --break-system-packages

echo 'Add Douyin_TikTok_Download_API to system service'

cp /www/wwwroot/Douyin_TikTok_Download_API/daemon/* /etc/systemd/system/

systemctl enable Douyin_TikTok_Download_API.service

echo 'Starting Douyin_TikTok_Download_API service'

systemctl start Douyin_TikTok_Download_API.service

