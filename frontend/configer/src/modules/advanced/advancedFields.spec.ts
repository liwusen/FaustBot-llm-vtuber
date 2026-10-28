import { describe, expect, it } from 'vitest';
import { bucketModuleFields, pickUnclassifiedFields } from './advancedFields';

describe('pickUnclassifiedFields', () => {
  it('sorts keys and excludes AI / LIVE2D / SPEECH / module-managed keys', () => {
    const fields = pickUnclassifiedFields(
      {
        ZZZ_CUSTOM: 1,
        AAA_CUSTOM: 'x',
        EMBED_MODEL: 'm',
        LIVE2D_MODEL_PATH: 'p',
        TTS_MODE: 'openai',
        mcp_servers: [],
        MC_OPERATOR_URL: 'u',
        MC_EVENT_TRIGGER_ENABLED: true,
        TTS_CHUNK_IDEAL_TOKENS: 30,
      },
      {
        SEARCH_API_KEY: '***',
        MIMO_API_KEY: '***',
        CUSTOM_SECRET: 'x',
        AAA_SECRET: 'y',
      },
    );

    expect(fields.publicKeys).toEqual(['AAA_CUSTOM', 'ZZZ_CUSTOM']);
    expect(fields.privateKeys).toEqual(['AAA_SECRET', 'CUSTOM_SECRET']);
  });

  it('tolerates missing config dicts', () => {
    expect(pickUnclassifiedFields({}, {})).toEqual({ publicKeys: [], privateKeys: [] });
  });
});

describe('bucketModuleFields', () => {
  it('splits basic and advanced keys while keeping order', () => {
    const buckets = bucketModuleFields({
      publicKeys: ['B_KEY', 'OPENAI_TTS_SPEED', 'A_KEY', 'MM_BRIDGE_MAX_PIXELS'],
      privateKeys: ['P_KEY', 'EDGE_TTS_RATE'],
    });

    expect(buckets.basicPublic).toEqual(['B_KEY', 'A_KEY']);
    expect(buckets.advancedPublic).toEqual(['OPENAI_TTS_SPEED', 'MM_BRIDGE_MAX_PIXELS']);
    expect(buckets.basicPrivate).toEqual(['P_KEY']);
    expect(buckets.advancedPrivate).toEqual(['EDGE_TTS_RATE']);
    expect(buckets.advancedCount).toBe(3);
  });

  it('reports zero advanced fields', () => {
    const buckets = bucketModuleFields({ publicKeys: ['X'], privateKeys: [] });
    expect(buckets).toMatchObject({
      basicPublic: ['X'],
      basicPrivate: [],
      advancedPublic: [],
      advancedPrivate: [],
      advancedCount: 0,
    });
  });
});
