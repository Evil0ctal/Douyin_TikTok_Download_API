#!/bin/bash

echo 'installing  Git...'

apt-get install git

echo 'installing  Python3...'

apt install python3

echo 'installing  PIP3...'

apt install python3-pip

echo 'installing  NodeJS...'

apt install nodejs

echo 'Creating path: /www/wwwroot'

mkdir -p /www/wwwroot

cd /www/wwwroot || exit

echo 'Cloning Douyin_TikTok_Download_API.git from Github!'

git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git

cd Douyin_TikTok_Download_API/ || exit

pip install -r requirements.txt

echo 'Please edit config.ini, all input must be numbers!'

python3 config.py

cp /www/wwwroot/Douyin_TikTok_Download_API/daemon/* /etc/systemd/system/

read -r -p "Run API or Web? [api/web/all/quit] " input
case $input in
    [aA][pP][iI]|[aA])
        read -r -p "Do you want to start the api service when system boot? [y/n] " input
        case $input in
            [yY])
          systemctl enable web_api.service
          echo "API service will start when system boot!"
          ;;
            [nN]| *)
          echo "You can start the service by running: systemctl start web_api.service"
          ;;
        esac
        echo "Starting API..."
		    systemctl start web_api.service
		    echo "API is running! You can visit http://your_ip:port"
		    echo "You can stop the api service by running: systemctl stop web_api.service"
        ;;
    [wW][eE][bB]|[wW])
        read -r -p "Do you want to start the app service when system boot? [y/n] " input
        case $input in
            [yY])
          systemctl enable web_app.service
          echo "Web service will start when system boot!"
          ;;
            [nN]| *)
          echo "You can start the service by running: systemctl start web_app.service"
          ;;
        esac
        echo "Staining APP..."
		    systemctl start web_app.service
		    echo "API is running! You can visit http://your_ip:port"
		    echo "You can stop the api service by running: systemctl stop web_app.service"
        ;;
    [aA][lL][lL])
      read -r -p "Do you want to start the app and api service when system boot? [y/n] " input
        case $input in
            [yY])
          systemctl enable web_app.service
          systemctl enable web_api.service
          ;;
            [nN]| *)
          echo "You can start them on boot by these commands:"
          echo  "systemctl enable (web_app.service||web_api.service)"
          ;;
        esac
        echo "Starting WEB and API Services..."
        systemctl start web_app.service
		    systemctl start web_api.service
		    echo "API and APP service are running!"
		    echo "You can stop the api service by running following command: "
		    echo "systemctl stop (web_app.service||web_api.service)"
        ;;
    *)
        echo "Exiting without running anything..."
        exit 1
        ;;
esac

