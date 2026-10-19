/**
 * The password floor, mirrored for the console.
 *
 * The authority is `MIN_PASSWORD_LENGTH` in `src/dtk/api/routes/passwords.py`,
 * which every route that sets a password validates against;
 * tests/unit/test_password_rule.py holds this copy to it. There used to be two
 * console copies that disagreed: the setup wizard asked the first administrator
 * for 12 characters while the server, the CLI and the Users page accepted 8, so
 * the one rule a new instance stated was a rule nothing else enforced.
 */
export const MIN_PASSWORD_LENGTH = 8
