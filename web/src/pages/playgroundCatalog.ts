/**
 * The Playground's endpoint catalogue, and what each platform does with it.
 *
 * Its own module because the page had grown past 1,800 lines with this table at
 * the top of it, and because nothing here renders: it is the description of the
 * API the form is generated from, and the rules for reading it per platform.
 *
 * The platforms are not one API with three hosts. Past the core five they serve
 * different endpoints, name their ids differently, page in different sizes and
 * answer some endpoints only about the identity asking. The catalogue used to
 * describe Douyin and leave the reader to find the rest out from refusals: a
 * Bilibili reader saw seven endpoints that answer UNSUPPORTED_CONTENT, Douyin
 * placeholders on every field, and a page size of 50 the platform refuses above
 * 20. Everything below that varies by platform says so.
 */

import { API_V1, paths } from '@/lib/endpoints'
import { PAGE_CEILING, type Platform, type UserRole } from '@/lib/types'

export type ParamKind = 'text' | 'number' | 'boolean' | 'choice'
export type ParamWhere = 'query' | 'body'
/** Which block of the form a parameter belongs in. Purely presentational. */
export type ParamSection = 'target' | 'paging' | 'request'

/** A value that may differ per platform. A plain value is the same on all. */
export type PerPlatform<T> = T | Readonly<Record<Platform, T>>

export interface ParamDef {
  /** Wire name. Never translated (docs/design/14-i18n.md). */
  name: string
  kind: ParamKind
  where: ParamWhere
  section: ParamSection
  required?: boolean
  /** Members of a group are alternatives: exactly one has to be filled in. */
  oneOf?: string
  /**
   * The platforms whose endpoint actually takes this parameter. Absent means
   * all of them.
   *
   * The platforms are not symmetric: Douyin's bookmark-folder list carries no
   * user id and answers only about the identity sending it, so an author there
   * is refused rather than ignored. Hiding the control is what stops that
   * refusal from being how a reader finds out.
   */
  platforms?: readonly Platform[]
  placeholder?: PerPlatform<string>
  min?: number
  /** The page size used to be capped at 50 everywhere, which TikTok refuses
   *  above 35 and Bilibili above 20 - so the form passed a value the API then
   *  answered with a 400. */
  max?: PerPlatform<number>
  defaultValue?: string
  /** A `choice` parameter's values. Leaving it unset sends nothing: the API's own default. */
  choices?: readonly string[]
  /** Catalogue key of the description, when it is not `playground.param.<name>`. */
  descriptionKey?: string
  /** Platforms whose ids look different enough to need their own description. */
  descriptions?: Partial<Readonly<Record<Platform, string>>>
  /**
   * The role this parameter needs, when it needs more than reading does.
   *
   * Disables the control and keeps the value out of the request. It is not the
   * gate - `resolve_explain` and `resolve_request_identity` are, and they run
   * again for every call whatever the console did. What this buys is that a
   * demo visitor is told the parameter needs an operator instead of ticking a
   * box and being answered 403 by a workbench whose whole job is to make a
   * request legible.
   */
  minRole?: UserRole
}

/** What "Load example" fills in. Absent when the endpoint needs an id nobody
 *  can be given in advance - a comment, a folder, a mix. */
export type ExampleKind = 'post' | 'author'

export interface EndpointDef {
  id: string
  method: 'GET' | 'POST'
  /** Path template, rendered with the selected platform. */
  path: (platform: Platform) => string
  /** Scheduler endpoint suffix used to look health up, or null for the front door. */
  operation: string | null
  /**
   * The operation a given form actually calls, when a parameter decides it.
   * The popular boards are four upstream operations behind one path, each
   * with its own circuit; the health shown has to be the board's own.
   */
  operationFor?: (values: Readonly<Record<string, string>>) => string
  platformScoped: boolean
  /**
   * Platforms known to serve this without asking. The capability table is the
   * authority once it has answered; this is what stands in while it loads, or
   * against an API too old to publish one.
   */
  platforms?: readonly Platform[]
  example?: ExampleKind
  params: ParamDef[]
}

/** Resolve a per-platform value. */
export function forPlatform<T>(value: PerPlatform<T> | undefined, platform: Platform): T | undefined {
  if (value === undefined) return undefined
  if (typeof value === 'object' && value !== null && platform in value) {
    return (value as Readonly<Record<Platform, T>>)[platform]
  }
  return value as T
}

/* -------------------------------------------------------------------------- */
/* Placeholders and examples                                                   */
/* -------------------------------------------------------------------------- */

/**
 * Real, public targets, one set per platform, for "Load example".
 *
 * Placeholders show the shape of an id; these have to work, because a reader
 * pressing the button is asking "show me one call that succeeds". Douyin's and
 * TikTok's posts are the ones the README has always used, Douyin's author is
 * the account v4's reference used, TikTok's author is the public profile the
 * signing example already names, and Bilibili's are the video and the space the
 * README and the user guide use (`BV1xx411c7mD`, `space.bilibili.com/2`).
 *
 * Authors are filled in as an id rather than a link. A TikTok profile link
 * names an @handle, which only the profile lookup accepts; every author list
 * there refuses it, so a link would make the example fail on four endpoints.
 */
export interface PlatformExample {
  postUrl: string
  authorId: string
}

export const EXAMPLES: Readonly<Record<Platform, PlatformExample>> = {
  douyin: {
    postUrl: 'https://www.douyin.com/video/7126745726494821640',
    authorId: 'MS4wLjABAAAANXSltcLCzDGmdNFI2Q_QixVTr67NiYzjKOIP5s03CAE',
  },
  tiktok: {
    postUrl: 'https://www.tiktok.com/@evil0ctal/video/7156033831819037994',
    authorId: 'MS4wLjABAAAAqB08cUbXaDWqbD6MCga2RbGTuhfO2EsHayBYx08NDrN7IE3jQuRDNNN6YwyfH6_6',
  },
  bilibili: {
    postUrl: 'https://www.bilibili.com/video/BV1xx411c7mD',
    authorId: '2',
  },
}

const POST_URL_PLACEHOLDER: Readonly<Record<Platform, string>> = {
  douyin: 'https://www.douyin.com/video/7300000000000000000',
  tiktok: 'https://www.tiktok.com/@user/video/7300000000000000000',
  bilibili: 'https://www.bilibili.com/video/BV1xx411c7mD',
}

const PROFILE_URL_PLACEHOLDER: Readonly<Record<Platform, string>> = {
  douyin: 'https://www.douyin.com/user/MS4wLjABAAAA…',
  tiktok: 'https://www.tiktok.com/@user',
  bilibili: 'https://space.bilibili.com/2',
}

/** A share link where the platform has one; Bilibili's b23.tv codes are
 *  opaque, so its canonical link shows the shape better. */
const SHARE_LINK_PLACEHOLDER: Readonly<Record<Platform, string>> = {
  douyin: 'https://v.douyin.com/iRNBho6G/',
  tiktok: 'https://www.tiktok.com/t/ZTR9nkkmL/',
  bilibili: 'https://www.bilibili.com/video/BV1xx411c7mD',
}

/* -------------------------------------------------------------------------- */
/* Parameters                                                                  */
/* -------------------------------------------------------------------------- */

const URL_PARAM: ParamDef = {
  name: 'url',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  placeholder: POST_URL_PLACEHOLDER,
}

const WAIT_PARAM: ParamDef = {
  name: 'wait',
  kind: 'number',
  where: 'query',
  section: 'request',
  min: 0,
  max: 30,
}
const CURSOR_PARAM: ParamDef = { name: 'cursor', kind: 'text', where: 'query', section: 'paging' }
const COUNT_PARAM: ParamDef = {
  name: 'count',
  kind: 'number',
  where: 'query',
  section: 'paging',
  min: 1,
  max: PAGE_CEILING,
}
/**
 * Accepted by every read endpoint since the page endpoints gained it. It used
 * to be declared on three, while the response panel offered an "include the raw
 * payload and run again" button on all of them - which re-sent the identical
 * request, because the builder only walks the selected endpoint's parameters.
 */
export const RAW_PARAM: ParamDef = {
  name: 'include_raw',
  kind: 'boolean',
  where: 'query',
  section: 'request',
  defaultValue: 'true',
}
/**
 * Every data endpoint accepts one, and every instance refuses it until an
 * operator sets security.request_proxy. Offering the field anyway is the point:
 * a refusal here names the setting, which is more use than the field not
 * existing.
 */
const PROXY_PARAM: ParamDef = {
  name: 'proxy',
  kind: 'text',
  where: 'query',
  section: 'request',
  placeholder: 'http://user:pass@host:port',
}
/**
 * Send as one named identity and no other. Rendered as a picker rather than as
 * a text input, because the value is a uuid nobody types from memory and the
 * thing a reader is actually choosing between is "which of my accounts".
 *
 * Optional here and required per endpoint: the capability table says which
 * endpoints answer only about the identity asking, and the page marks it
 * required there rather than letting the 400 say so.
 */
export const IDENTITY_PARAM: ParamDef = {
  name: 'identity',
  kind: 'text',
  where: 'query',
  section: 'request',
  // Pinning reveals which jar served a call and lets a caller aim at one
  // account, so the API gates it exactly as `explain` is gated.
  minRole: 'operator',
}
/**
 * Ask again for real. Two separate mechanisms make a repeat call cheap - the
 * task layer joins an identical run that is already going or has just
 * finished, and the fetch layer caches the shaped body - and both are correct
 * defaults right up until the question is "has this changed since I last
 * looked", which is the question a workbench exists to answer.
 */
const REFRESH_PARAM: ParamDef = {
  name: 'refresh',
  kind: 'boolean',
  where: 'query',
  section: 'request',
}
const SEC_UID_PARAM: ParamDef = {
  name: 'sec_user_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  // A placeholder of "2" read as a filled-in value; a longer number reads as
  // the shape of a mid.
  placeholder: { douyin: 'MS4wLjABAAAA…', tiktok: 'MS4wLjABAAAA…', bilibili: '12345678' },
  descriptions: {
    tiktok: 'playground.paramFor.tiktok.sec_user_id',
    bilibili: 'playground.paramFor.bilibili.sec_user_id',
  },
}
const PROFILE_URL_PARAM: ParamDef = {
  ...URL_PARAM,
  placeholder: PROFILE_URL_PLACEHOLDER,
  descriptions: { tiktok: 'playground.paramFor.tiktok.profileUrl' },
}
//? Douyin's listcollection takes no author at all; see ParamDef.platforms.
const TIKTOK_ONLY_PROFILE_URL_PARAM: ParamDef = { ...PROFILE_URL_PARAM, platforms: ['tiktok'] }
const TIKTOK_ONLY_SEC_UID_PARAM: ParamDef = { ...SEC_UID_PARAM, platforms: ['tiktok'] }
//? Folders: TikTok and Bilibili are asked about an author, Douyin about the session.
const FOLDER_OWNER_URL_PARAM: ParamDef = { ...PROFILE_URL_PARAM, platforms: ['tiktok', 'bilibili'] }
const FOLDER_OWNER_SEC_UID_PARAM: ParamDef = { ...SEC_UID_PARAM, platforms: ['tiktok', 'bilibili'] }
const AWEME_ID_PARAM: ParamDef = {
  name: 'aweme_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  placeholder: {
    douyin: '7300000000000000000',
    tiktok: '7300000000000000000',
    bilibili: 'BV1xx411c7mD',
  },
  descriptions: { bilibili: 'playground.paramFor.bilibili.aweme_id' },
}
const COLLECTION_ID_PARAM: ParamDef = {
  name: 'collection_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  required: true,
}

/**
 * Ask what request this instance actually made.
 *
 * Costs a cache miss - an explanation describes an attempt, and a cached answer
 * made none - and returns the identity's own cookie jar, so it is gated on the
 * console's own role rather than on a read scope. Off by default for both
 * reasons; the panels below say what turning it on buys.
 */
export const EXPLAIN_PARAM: ParamDef = {
  name: 'explain',
  kind: 'boolean',
  where: 'query',
  section: 'request',
  minRole: 'operator',
}

/** The ones every read endpoint carries, in the order the API declares them. */
const ENVELOPE_PARAMS: readonly ParamDef[] = [
  RAW_PARAM,
  WAIT_PARAM,
  PROXY_PARAM,
  IDENTITY_PARAM,
  REFRESH_PARAM,
  EXPLAIN_PARAM,
]
const PAGE_PARAMS: readonly ParamDef[] = [CURSOR_PARAM, COUNT_PARAM]
//? Bilibili only: which part of a video uploaded in several. A link's ?p= says the same.
const PART_PARAM: ParamDef = {
  name: 'part',
  kind: 'number',
  where: 'query',
  section: 'target',
  min: 1,
  max: 1000,
  platforms: ['bilibili'],
}
//? Bilibili only: Douyin and TikTok rank comments their own way and refuse `new`.
const SORT_PARAM: ParamDef = {
  name: 'sort',
  kind: 'choice',
  where: 'query',
  section: 'paging',
  choices: ['hot', 'new'],
  platforms: ['bilibili'],
}

/**
 * Bilibili's front-page lists. Four upstream operations behind one path, each
 * with its own circuit, so the board decides which health row is this call's.
 */
const BOARD_OPERATIONS: Readonly<Record<string, string>> = {
  hot: 'popular',
  weekly: 'popular_weekly',
  precious: 'popular_precious',
  rank: 'ranking',
}
const BOARD_PARAM: ParamDef = {
  name: 'board',
  kind: 'choice',
  where: 'query',
  section: 'target',
  choices: Object.keys(BOARD_OPERATIONS),
}
/** The same wire name, meaning three different things depending on the board. */
const BOARD_CURSOR_PARAM: ParamDef = {
  ...CURSOR_PARAM,
  descriptionKey: 'playground.param.boardCursor',
}
/** Read by `hot` alone: the other boards come back 50, 100 or so at a time. */
const BOARD_COUNT_PARAM: ParamDef = {
  ...COUNT_PARAM,
  descriptionKey: 'playground.param.boardCount',
}

/** Read by Douyin and TikTok only: Bilibili answers no sound and no hashtag. */
const SOUND_PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']

//? A sound's page. TikTok's link carries the title before the id; only the id is read.
const MUSIC_URL_PARAM: ParamDef = {
  ...URL_PARAM,
  platforms: SOUND_PLATFORMS,
  placeholder: {
    douyin: 'https://www.douyin.com/music/7300000000000000000',
    tiktok: 'https://www.tiktok.com/music/original-sound-7300000000000000000',
    bilibili: '',
  },
  descriptionKey: 'playground.param.musicUrl',
}
const MUSIC_ID_PARAM: ParamDef = {
  name: 'music_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  platforms: SOUND_PLATFORMS,
  placeholder: '7300000000000000000',
}
//? A Douyin hashtag link numbers it; a TikTok tag link names it.
const HASHTAG_URL_PARAM: ParamDef = {
  ...URL_PARAM,
  placeholder: {
    douyin: 'https://www.douyin.com/hashtag/1592544802272436',
    tiktok: 'https://www.tiktok.com/tag/fyp',
    bilibili: '',
  },
  descriptionKey: 'playground.param.hashtagUrl',
}
const HASHTAG_ID_PARAM: ParamDef = {
  name: 'hashtag_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  placeholder: '1592544802272436',
}
//? TikTok only: Douyin looks a hashtag up by its id alone and refuses a name.
const HASHTAG_NAME_PARAM: ParamDef = {
  name: 'name',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  platforms: ['tiktok'],
  // eslint-disable-next-line local/no-untranslated-text -- a hashtag name, the same in any language
  placeholder: 'fyp',
  descriptionKey: 'playground.param.hashtagName',
}
const KEYWORD_PARAM: ParamDef = {
  name: 'keyword',
  kind: 'text',
  where: 'query',
  section: 'target',
  required: true,
}
//? A room is addressed the way its link addresses it, which differs by platform.
const LIVE_URL_PARAM: ParamDef = {
  ...URL_PARAM,
  placeholder: {
    douyin: 'https://live.douyin.com/615171448842',
    tiktok: 'https://www.tiktok.com/@user/live',
    bilibili: 'https://live.bilibili.com/173551',
  },
  descriptionKey: 'playground.param.liveUrl',
}
const ROOM_ID_PARAM: ParamDef = {
  name: 'room_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  placeholder: { douyin: '615171448842', tiktok: 'user', bilibili: '173551' },
  descriptions: { tiktok: 'playground.paramFor.tiktok.room_id' },
}
/** The broadcast's own id: what a Douyin app share link carries and what a
 *  room answer hands back as live_id. Bilibili numbers a room once. */
const LIVE_ID_PARAM: ParamDef = {
  name: 'live_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  oneOf: 'target',
  platforms: ['douyin', 'tiktok'],
  placeholder: { douyin: '7300000000000000101', tiktok: '7400000000000000101', bilibili: '' },
}
/** The same id, as the one thing a ranking is asked by. */
const RANKING_LIVE_ID_PARAM: ParamDef = {
  name: 'live_id',
  kind: 'text',
  where: 'query',
  section: 'target',
  required: true,
  platforms: ['douyin'],
  placeholder: { douyin: '7300000000000000101', tiktok: '', bilibili: '' },
  descriptionKey: 'playground.param.rankingLiveId',
}
const LIVE_IDS_PARAM: ParamDef = {
  name: 'live_ids',
  kind: 'text',
  where: 'query',
  section: 'target',
  required: true,
  placeholder: {
    douyin: '7300000000000000101,7300000000000000102',
    tiktok: '7400000000000000101,7400000000000000102',
    bilibili: '',
  },
}
const TRENDING_BOARD_PARAM: ParamDef = {
  name: 'board',
  kind: 'choice',
  where: 'query',
  section: 'target',
  choices: ['hot', 'entertainment', 'society', 'challenge'],
  descriptionKey: 'playground.param.trendingBoard',
}

/* -------------------------------------------------------------------------- */
/* The catalogue                                                               */
/* -------------------------------------------------------------------------- */

export const CATALOG: readonly EndpointDef[] = [
  {
    id: 'parse',
    method: 'POST',
    path: () => paths.parse,
    operation: null,
    platformScoped: false,
    example: 'post',
    params: [
      {
        name: 'url',
        kind: 'text',
        where: 'body',
        section: 'target',
        required: true,
        placeholder: SHARE_LINK_PLACEHOLDER,
      },
      { ...RAW_PARAM, where: 'body' },
      WAIT_PARAM,
      PROXY_PARAM,
      IDENTITY_PARAM,
      REFRESH_PARAM,
      EXPLAIN_PARAM,
    ],
  },
  {
    id: 'video',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video`,
    operation: 'content_detail',
    platformScoped: true,
    example: 'post',
    params: [URL_PARAM, AWEME_ID_PARAM, PART_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'parts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/parts`,
    operation: 'content_parts',
    platformScoped: true,
    platforms: ['bilibili'],
    example: 'post',
    params: [URL_PARAM, AWEME_ID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'comments',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/comments`,
    operation: 'comments',
    platformScoped: true,
    example: 'post',
    params: [URL_PARAM, AWEME_ID_PARAM, ...PAGE_PARAMS, SORT_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'replies',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/comments/replies`,
    operation: 'comment_replies',
    platformScoped: true,
    params: [
      { name: 'comment_id', kind: 'text', where: 'query', section: 'target', required: true },
      { ...AWEME_ID_PARAM, oneOf: undefined },
      ...PAGE_PARAMS,
      ...ENVELOPE_PARAMS,
    ],
  },
  {
    id: 'user',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user`,
    operation: 'author_profile',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'posts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/posts`,
    operation: 'author_posts',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'likes',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/likes`,
    operation: 'author_likes',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'reposts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/reposts`,
    operation: 'author_reposts',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'collections',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/collections`,
    operation: 'author_collections',
    platformScoped: true,
    example: 'author',
    params: [
      FOLDER_OWNER_URL_PARAM,
      FOLDER_OWNER_SEC_UID_PARAM,
      ...PAGE_PARAMS,
      ...ENVELOPE_PARAMS,
    ],
  },
  {
    id: 'bookmarks',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/bookmarks`,
    operation: 'author_bookmarks',
    platformScoped: true,
    example: 'author',
    params: [
      TIKTOK_ONLY_PROFILE_URL_PARAM,
      TIKTOK_ONLY_SEC_UID_PARAM,
      ...PAGE_PARAMS,
      ...ENVELOPE_PARAMS,
    ],
  },
  {
    id: 'collectionDetail',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/collection`,
    operation: 'collection_detail',
    platformScoped: true,
    params: [COLLECTION_ID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'collectionPosts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/collection/posts`,
    operation: 'collection_posts',
    platformScoped: true,
    params: [COLLECTION_ID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'mix',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/mix/posts`,
    operation: 'mix_posts',
    platformScoped: true,
    params: [
      { name: 'mix_id', kind: 'text', where: 'query', section: 'target', required: true },
      ...PAGE_PARAMS,
      ...ENVELOPE_PARAMS,
    ],
  },
  {
    id: 'followers',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/followers`,
    operation: 'author_followers',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'following',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/following`,
    operation: 'author_following',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'popular',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/popular`,
    operation: 'popular',
    operationFor: (values) => BOARD_OPERATIONS[values['board'] || 'hot'] ?? 'popular',
    platformScoped: true,
    platforms: ['bilibili'],
    params: [BOARD_PARAM, BOARD_CURSOR_PARAM, BOARD_COUNT_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'related',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/related`,
    operation: 'related_posts',
    platformScoped: true,
    example: 'post',
    params: [URL_PARAM, AWEME_ID_PARAM, COUNT_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'mixes',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/mixes`,
    operation: 'author_mixes',
    platformScoped: true,
    example: 'author',
    params: [PROFILE_URL_PARAM, SEC_UID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'music',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/music`,
    operation: 'music_detail',
    platformScoped: true,
    platforms: SOUND_PLATFORMS,
    params: [MUSIC_URL_PARAM, MUSIC_ID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'musicPosts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/music/posts`,
    operation: 'music_posts',
    platformScoped: true,
    platforms: SOUND_PLATFORMS,
    params: [MUSIC_URL_PARAM, MUSIC_ID_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'hashtag',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/hashtag`,
    operation: 'hashtag_detail',
    platformScoped: true,
    platforms: SOUND_PLATFORMS,
    params: [HASHTAG_URL_PARAM, HASHTAG_ID_PARAM, HASHTAG_NAME_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'hashtagPosts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/hashtag/posts`,
    operation: 'hashtag_posts',
    platformScoped: true,
    platforms: ['tiktok'],
    params: [
      { ...HASHTAG_URL_PARAM, descriptionKey: 'playground.param.hashtagPostsUrl' },
      HASHTAG_ID_PARAM,
      ...PAGE_PARAMS,
      ...ENVELOPE_PARAMS,
    ],
  },
  {
    id: 'searchPosts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/search/posts`,
    operation: 'search_posts',
    platformScoped: true,
    platforms: ['tiktok', 'bilibili'],
    params: [KEYWORD_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'searchUsers',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/search/users`,
    operation: 'search_authors',
    platformScoped: true,
    platforms: ['tiktok', 'bilibili'],
    params: [KEYWORD_PARAM, ...PAGE_PARAMS, ...ENVELOPE_PARAMS],
  },
  {
    id: 'live',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/live`,
    operation: 'live_room',
    platformScoped: true,
    params: [LIVE_URL_PARAM, ROOM_ID_PARAM, LIVE_ID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'liveRanking',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/live/ranking`,
    operation: 'live_ranking',
    platformScoped: true,
    platforms: ['douyin'],
    params: [RANKING_LIVE_ID_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'liveStatus',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/live/status`,
    operation: 'live_status',
    platformScoped: true,
    platforms: ['douyin', 'tiktok'],
    params: [LIVE_IDS_PARAM, ...ENVELOPE_PARAMS],
  },
  {
    id: 'trending',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/trending`,
    operation: 'trending',
    platformScoped: true,
    platforms: ['douyin', 'bilibili'],
    params: [TRENDING_BOARD_PARAM, ...ENVELOPE_PARAMS],
  },
]

export const DEFAULT_ENDPOINT = CATALOG[0] as EndpointDef

export const SECTION_ORDER: readonly ParamSection[] = ['target', 'paging', 'request']

/* -------------------------------------------------------------------------- */
/* What a platform serves                                                      */
/* -------------------------------------------------------------------------- */

/** One row of `GET /system/capabilities`. */
export interface CapabilityRow {
  operation: string
  endpoint: string
  /** Answers only about the identity sending it, so `identity` is required. */
  session_bound: boolean
  /**
   * What a guest identity loses here: on the first page, and on every later
   * one (`paging_login`, null where later pages are no different). Absent from
   * an API older than the per-endpoint login table.
   */
  login?: LoginNeed
  paging_login?: LoginNeed | null
}

/** `none`: a guest gets the same answer; `preferred`: less; `required`: nothing. */
export type LoginNeed = 'none' | 'preferred' | 'required'

/** Keyed by platform. A platform this console does not know is ignored. */
export type CapabilityTable = Readonly<Record<string, readonly CapabilityRow[] | undefined>>

export interface Support {
  /** Null while the table is unknown: everything is offered, as before it existed. */
  served: boolean | null
  sessionBound: boolean
}

/**
 * Whether `platform` serves this endpoint, and on what terms.
 *
 * `operation` defaults to the endpoint's own, and is the board-specific one
 * when the form has picked a board. The front door is served everywhere: it
 * works out the platform from the link.
 */
export function supportOf(
  endpoint: EndpointDef,
  platform: Platform,
  table: CapabilityTable | undefined,
  operation: string | null = endpoint.operation,
): Support {
  if (!endpoint.platformScoped || operation === null) return { served: true, sessionBound: false }
  const rows = table?.[platform]
  if (!rows) {
    return {
      served: endpoint.platforms ? endpoint.platforms.includes(platform) : null,
      sessionBound: false,
    }
  }
  const row = rows.find((entry) => entry.operation === operation)
  return { served: row !== undefined, sessionBound: row?.session_bound ?? false }
}

/**
 * The values "Load example" puts in the form, or null when this endpoint has
 * nothing an example could fill on this platform.
 *
 * Only parameters the platform's endpoint takes are filled: Douyin's folder
 * list has no author parameter, and filling a hidden one would send it.
 */
export function exampleValues(
  endpoint: EndpointDef,
  platform: Platform,
): Record<string, string> | null {
  const example = EXAMPLES[platform]
  const takes = (name: string): boolean =>
    endpoint.params.some(
      (param) => param.name === name && (!param.platforms || param.platforms.includes(platform)),
    )

  if (endpoint.example === 'post' && takes('url')) {
    return { url: example.postUrl, aweme_id: '' }
  }
  if (endpoint.example === 'author' && takes('sec_user_id')) {
    return { sec_user_id: example.authorId, url: '' }
  }
  return null
}
