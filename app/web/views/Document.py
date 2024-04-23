from pywebio.output import popup, put_markdown, put_html, put_text, put_link
from app.web.views.ViewsUtils import ViewsUtils

t = ViewsUtils().t


# API文档弹窗/API documentation pop-up
def api_document_pop_window():
    with popup(t("📑API文档", "📑API Document")):
        put_markdown(t("> 介绍",
                       "> Introduction"))
        put_markdown(t("你可以利用本项目提供的API接口来获取抖音/TikTok的数据，具体接口文档请参考下方链接。",
                       "You can use the API provided by this project to obtain Douyin/TikTok data. For specific API documentation, please refer to the link below."))
        put_markdown(t("如果API不可用，请尝试自己部署本项目，然后再配置文件中修改cookie的值。",
                       "If the API is not available, please try to deploy this project by yourself, and then modify the value of the cookie in the configuration file."))
        put_link('[API Docs]', '/docs', new_window=True)
        put_markdown("----")
        put_markdown(t("> 更多接口",
                       "> More APIs"))
        put_markdown(t("如果你想要使用更多且更稳定的API服务，可以使用付费API服务",
                       "If you want to use more and more stable API services, you can use paid API services"))
        put_link('[TikHub API]', 'https://api.tikhub.io', new_window=True)
        put_markdown("----")
        put_markdown(t("> 限时免费测试",
                       "> Free test for a limited time"))
        put_markdown(t("这里也有一个测试版的API服务，你可以直接免费使用",
                       "There is also a beta version of the API service, which you can use for free"))
        put_markdown(t("测试接口只会保留一段时间，不保证数据的稳定性",
                       "The test interface will only be retained for a period of time, and the stability of the data is not guaranteed"))
        put_link('[TikHub Beta API]', 'https://beta.tikhub.io', new_window=True)
