/**
 * The data layer's front door.
 *
 * Each module owns one area — projects, ingest, traces, analytics, entities,
 * scores, prompts, datasets — and this re-exports them, so a page can import
 * several queries on one line without knowing which file each lives in.
 */

export * from './analytics';
export * from './datasets';
export * from './entities';
export * from './ingest';
export * from './projects';
export * from './prompts';
export * from './scores';
export * from './traces';
