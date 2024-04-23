from pywebio.output import popup, put_markdown, put_html, put_text, put_link, put_image
from app.web.views.ViewsUtils import ViewsUtils

t = ViewsUtils().t


# 关于弹窗/About pop-up
def about_pop_window():
    with popup(t('更多信息', 'More Information')):
        put_html('<h3>👀{}</h3>'.format(t('访问记录', 'Visit Record')))
        put_image('https://views.whatilearened.today/views/github/evil0ctal/TikTokDownload_PyWebIO.svg',
                  title='访问记录')
        put_html('<hr>')
        put_html('<h3>⭐Github</h3>')
        put_markdown('[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)')
        put_html('<hr>')
        put_html('<h3>🎯{}</h3>'.format(t('反馈', 'Feedback')))
        put_markdown('{}：[issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)'.format(
            t('Bug反馈', 'Bug Feedback')))
        put_html('<hr>')
        put_html('<h3>💖WeChat</h3>')
        put_markdown('WeChat：[Evil0ctal](https://mycyberpunk.com/)')
        put_html('<hr>')
