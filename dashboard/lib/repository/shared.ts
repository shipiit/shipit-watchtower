/** Helpers every query module needs. */

export const randomId = () => crypto.randomUUID().replaceAll('-', '');

/** Neutralise LIKE wildcards in user input; pair with `ESCAPE '\'`. */
export const escapeLike = (value: string) => value.replace(/[\\%_]/g, (match) => `\\${match}`);
