#!/bin/sh

# Activating the virtual environment
# shellcheck disable=SC2039
source /www/wwwroot/Douyin_TikTok_Download_API/venv/bin/activate

# Starting the Python application
python start.py

# Deactivating the virtual environment (optional, since the script is ending)
deactivate
