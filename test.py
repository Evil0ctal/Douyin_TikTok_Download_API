from crawlers.tiktok.web.web_crawler import TikTokWebCrawler  # Import TikTokWebCrawler class
import asyncio
import json
import time

api = TikTokWebCrawler()

async def main() -> dict:
    # count = 20
    # # posts = []
    # result = await api.fetch_post_comment("7426227701096123655", count)
    
    # # posts.extend(result.get("comments", []))

    # # Write initial result to the JSON file
    # for comment in result.get("comments", []):
    #     with open("cmt.txt", "a") as f:
    #         f.write(comment.get("text") + "\n")
    
    # # Fetch additional comments if available
    # while result.get("has_more", False) and result.get("cursor"):
    #     time.sleep(5)
    #     cursor = result.get("cursor")
    #     result = await api.fetch_post_comment("7426227701096123655", cursor=cursor)
    #     # posts.extend(result.get("comments", []))
    #     for comment in result.get("comments", []):
    #         with open("cmt.txt", "a") as f:
    #             f.write(comment.get("text") + "\n")
    

    result = await api.get_search_video("hieuthuhai", offset=0, count=20)
    print(result)
   

asyncio.run(main())
