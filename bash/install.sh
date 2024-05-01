#!/bin/bash

# Set script to exit on any errors.
set -e

echo 'Updating package lists... | 正在更新软件包列表...'
sudo apt-get update

echo 'Installing Git... | 正在安装Git...'
sudo apt-get install -y git

echo 'Installing Python3... | 正在安装Python3...'
sudo apt install -y python3

echo 'Installing PIP3... | 正在安装PIP3...'
sudo apt install -y python3-pip

echo 'Installing python3-venv... | 正在安装python3-venv...'
sudo apt install -y python3-venv

echo 'Creating path: /www/wwwroot | 正在创建路径: /www/wwwroot'
sudo mkdir -p /www/wwwroot

cd /www/wwwroot || { echo "Failed to change directory to /www/wwwroot | 无法切换到目录 /www/wwwroot"; exit 1; }

echo 'Cloning Douyin_TikTok_Download_API.git from Github! | 正在从Github克隆Douyin_TikTok_Download_API.git!'
sudo git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git

cd Douyin_TikTok_Download_API/ || { echo "Failed to change directory to Douyin_TikTok_Download_API | 无法切换到目录 Douyin_TikTok_Download_API"; exit 1; }

echo 'Creating a virtual environment | 正在创建虚拟环境'
python3 -m venv venv

echo 'Activating the virtual environment | 正在激活虚拟环境'
source venv/bin/activate

echo 'Setting pip to use the default PyPI index | 设置pip使用默认PyPI索引'
pip config set global.index-url https://pypi.org/simple/

echo 'Installing pip setuptools | 安装pip setuptools'
pip install setuptools

echo 'Installing dependencies from requirements.txt | 从requirements.txt安装依赖'
pip install -r requirements.txt

echo 'Deactivating the virtual environment | 正在停用虚拟环境'
deactivate

echo 'Adding Douyin_TikTok_Download_API to system service | 将Douyin_TikTok_Download_API添加到系统服务'
sudo cp daemon/* /etc/systemd/system/

echo 'Enabling Douyin_TikTok_Download_API service | 启用Douyin_TikTok_Download_API服务'
sudo systemctl enable Douyin_TikTok_Download_API.service

echo 'Starting Douyin_TikTok_Download_API service | 启动Douyin_TikTok_Download_API服务'
sudo systemctl start Douyin_TikTok_Download_API.service

echo 'Douyin_TikTok_Download_API installation complete! | Douyin_TikTok_Download_API安装完成!'
echo 'You can access the API at http://localhost:80 | 您可以在http://localhost:80访问API'
echo 'You can change the port in config.yaml under the /www/wwwroot/Douyin_TikTok_Download_API directory | 您可以在/www/wwwroot/Douyin_TikTok_Download_API目录下的config.yaml中更改端口'
echo 'If the API is not working, please change the cookie in config.yaml under the /www/wwwroot/Douyin_TikTok_Download_API/crawler/[Douyin/TikTok]/[APP/Web]/config.yaml directory | 如果API无法工作，请更改/www/wwwroot/Douyin_TikTok_Download_API/crawler/[Douyin/TikTok]/[APP/Web]/config.yaml目录下的cookie'
