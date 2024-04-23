import os
import yaml
from fastapi import APIRouter
from app.api.models.APIResponseModel import iOS_Shortcut


# 读取上级再上级目录的配置文件
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'config.yaml')
with open(config_path, 'r', encoding='utf-8') as file:
    config = yaml.safe_load(file)

router = APIRouter()


@router.get("/shortcut", response_model=iOS_Shortcut, summary="用于iOS快捷指令的版本更新信息/Version update information for iOS shortcuts")
async def get_shortcut():
    shortcut_config = config["iOS_Shortcut"]
    version = shortcut_config["iOS_Shortcut_Version"]
    update = shortcut_config['iOS_Shortcut_Update_Time']
    link = shortcut_config['iOS_Shortcut_Link']
    link_en = shortcut_config['iOS_Shortcut_Link_EN']
    note = shortcut_config['iOS_Shortcut_Update_Note']
    note_en = shortcut_config['iOS_Shortcut_Update_Note_EN']
    return iOS_Shortcut(version=str(version), update=update, link=link, link_en=link_en, note=note, note_en=note_en)