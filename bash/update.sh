#!/bin/bash

read -r -p "Do you want to update the project? [y/n] " input
        case $input in
            [yY])
          cd ..
          git pull
          echo "Restarting the service - systemctl restart web_app.service"
          systemctl restart web_app.service
          echo "Restarting the service - systemctl restart web_api.service"
          systemctl restart web_api.service
          echo "Successfully restarted all services!"
          ;;
            [nN]| *)
          echo "Exiting..."
          exit 1
          ;;
        esac