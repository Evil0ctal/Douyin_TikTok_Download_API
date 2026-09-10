/**
 * The role ladder, mirrored for the console.
 *
 * The authority is `ROLE_RANK` in `src/dtk/api/routes/support.py`; this copy
 * exists so a control can be disabled before it is pressed rather than after
 * the API has answered 403. It is a courtesy, never the gate: every endpoint
 * re-derives the caller's role from the credential that carried the request,
 * and a console that lied about this would change nothing about what the API
 * allows.
 *
 * Keep the two in step. `USER_ROLES` in `./types` is the same set of names, so
 * adding a role there without adding it here is a type error rather than a
 * silent rank of `undefined`.
 */

import type { UserRole } from './types'

export const ROLE_RANK: Readonly<Record<UserRole, number>> = {
  demo: 0,
  viewer: 1,
  operator: 2,
  admin: 3,
}

/**
 * Whether `role` sits at or above `minimum` on the ladder.
 *
 * A missing role is below everything. That is the safe direction: the session
 * query answers `null` while it is in flight and when nobody is signed in, and
 * treating "not known yet" as "allowed" would flash an enabled control for the
 * one frame before the answer arrives.
 */
export function atLeast(role: UserRole | null | undefined, minimum: UserRole): boolean {
  if (!role) return false
  return ROLE_RANK[role] >= ROLE_RANK[minimum]
}
