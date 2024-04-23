#!/bin/bash

read -r -p "Do you want to update Douyin_TikTok_Download_API? [y/n] " input
        case $input in
            [yY])
          cd ..
          git pull
          echo "Restarting Douyin_TikTok_Download_API service"
          systemctl restart Douyin_TikTok_Download_API.service
          echo "Successfully restarted all services!"
          ;;
            [nN]| *)
          echo "Exiting..."
          exit 1
          ;;
        esac