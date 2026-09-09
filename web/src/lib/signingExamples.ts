import type { Platform } from '@/lib/types'

/**
 * Ready-made inputs for the signing tool, one per platform.
 *
 * The signing form asks for a URL, a User-Agent and a cookie jar, and the
 * hardest part of using it is knowing what a real one looks like. These are
 * taken from actual browser requests - `/aweme/v1/web/query/user/` on Douyin and
 * `/api/post/item_list/` on TikTok - so the parameter names, the version codes
 * and the viewport values are the ones the sites really send.
 *
 * **Two things were removed on purpose, and the removals are the lesson.**
 *
 * The signature parameters are gone: `a_bogus`, `verifyFp` and `fp` on Douyin,
 * `X-Gnarly`, `X-Bogus` and `X-Dynosaur` on TikTok. Those are what this tool
 * computes. Leaving them in an example would suggest they are something you
 * paste, and pasting a stale one is the single most common way to get a 200
 * with an empty body.
 *
 * The credentials are gone: every real cookie value, `msToken`, `uifid`,
 * `webid`, `device_id` and `odinId`. A cookie jar is a live login. The jars
 * below carry the cookie *names* that matter - which is the actual question
 * somebody has when they look at this field - with values that are obviously
 * not real. Use `/tools/identity` to mint a jar that is.
 *
 * What is kept is public: TikTok's example points at `@taylorswift`, whose
 * `secUid` is on every page of that profile, so the example is runnable rather
 * than merely illustrative.
 */
export interface SigningExample {
  platform: Platform
  /** The API URL, business parameters only. */
  url: string
  userAgent: string
  /**
   * TikTok seals this into the signature, so it has to be the token the request
   * will really carry - an invented one is verified and fails. Empty here, and
   * empty is accepted; `/tools/identity` is where a real one comes from.
   */
  msToken: string
  /** Cookie names, with placeholder values. Never a real jar. */
  cookies: string
}

const CHROME_MAC =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'

/**
 * Douyin's profile query, as the web client sends it.
 *
 * Most of this is the browser describing itself - screen size, core count,
 * engine version - and all of it is hashed into `a_bogus`, which is why the
 * values have to be the ones the request will really carry rather than round
 * numbers.
 */
const DOUYIN_QUERY = [
  'device_platform=webapp',
  'aid=6383',
  'channel=channel_pc_web',
  'publish_video_strategy_type=2',
  'update_version_code=170400',
  'pc_client_type=1',
  'version_code=170400',
  'version_name=17.4.0',
  'cookie_enabled=true',
  'screen_width=1496',
  'screen_height=967',
  'browser_language=zh-CN',
  'browser_platform=MacIntel',
  'browser_name=Chrome',
  'browser_version=151.0.0.0',
  'browser_online=true',
  'engine_name=Blink',
  'engine_version=151.0.0.0',
  'os_name=Mac+OS',
  'os_version=10.15.7',
  'cpu_core_num=16',
  'device_memory=32',
  'platform=PC',
  'downlink=10',
  'effective_type=4g',
  'round_trip_time=100',
  'support_h265=1',
  'support_dash=1',
].join('&')

/**
 * TikTok's author feed. `secUid` names the account and is public.
 *
 * `/api/post/item_list/` is the one TikTok path that verifies the X-Dynosaur
 * environment report, which makes it the useful one to experiment with: the
 * other endpoints answer a mis-signed request normally, so a signature bug
 * shows up here and nowhere else.
 */
const TIKTOK_QUERY = [
  'aid=1988',
  'app_language=zh-Hans',
  'app_name=tiktok_web',
  'browser_language=zh-CN',
  'browser_name=Mozilla',
  'browser_online=true',
  'browser_platform=MacIntel',
  'channel=tiktok_web',
  'cookie_enabled=true',
  'count=16',
  'coverFormat=2',
  'cursor=0',
  'data_collection_enabled=false',
  'device_platform=web_pc',
  'focus_state=true',
  'history_len=3',
  'is_fullscreen=false',
  'is_page_visible=true',
  'language=zh-Hans',
  'os=mac',
  'priority_region=',
  'referer=',
  'region=US',
  'root_referer=',
  'screen_height=967',
  'screen_width=1496',
  'secUid=MS4wLjABAAAAqB08cUbXaDWqbD6MCga2RbGTuhfO2EsHayBYx08NDrN7IE3jQuRDNNN6YwyfH6_6',
  'tz_name=America%2FLos_Angeles',
  'user_is_login=false',
  'video_encoding=dash',
  'webcast_language=zh-Hans',
].join('&')

export const SIGNING_EXAMPLES: Readonly<Record<Platform, SigningExample>> = {
  douyin: {
    platform: 'douyin',
    url: `https://www.douyin.com/aweme/v1/web/query/user/?${DOUYIN_QUERY}`,
    userAgent: CHROME_MAC,
    msToken: '',
    // UIFID_TEMP is the one that matters beyond the session: Douyin computes
    // x-secsdk-web-signature over the visitor id inside it, so a jar without it
    // comes back with no headers and the sign-protected endpoints refuse.
    cookies:
      'ttwid=REPLACE_WITH_A_REAL_JAR; odin_tt=REPLACE_WITH_A_REAL_JAR; ' +
      'UIFID_TEMP=REPLACE_WITH_A_REAL_JAR',
  },
  tiktok: {
    platform: 'tiktok',
    url: `https://www.tiktok.com/api/post/item_list/?${TIKTOK_QUERY}`,
    userAgent: CHROME_MAC,
    msToken: '',
    cookies:
      'ttwid=REPLACE_WITH_A_REAL_JAR; tt_csrf_token=REPLACE_WITH_A_REAL_JAR; ' +
      'msToken=REPLACE_WITH_A_REAL_JAR',
  },
}
