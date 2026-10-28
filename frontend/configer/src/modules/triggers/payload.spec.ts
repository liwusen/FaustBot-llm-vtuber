import { describe, expect, it } from 'vitest';
import {
  buildTriggerSubmitPayload,
  buildTriggerUpdatePayload,
  type TriggerFormState,
} from './payload';

function form(overrides: Partial<TriggerFormState> = {}): TriggerFormState {
  return {
    id: 'report',
    type: 'interval',
    recallDescription: '日报',
    lifespan: '',
    removeWhen: '',
    priority: 'normal',
    runBackground: false,
    intervalSeconds: '60',
    target: '',
    evalCode: '',
    ...overrides,
  };
}

describe('buildTriggerUpdatePayload', () => {
  it('builds interval payload with interval_seconds and no other dynamic field', () => {
    expect(buildTriggerUpdatePayload({
      id: '  report  ',
      type: 'interval',
      recall_description: '日报',
      run_background: 1 as unknown as boolean,
      interval_seconds: '30',
      target: 'ignored',
      eval_code: 'ignored',
    })).toEqual({
      id: 'report',
      type: 'interval',
      recall_description: '日报',
      run_background: true,
      interval_seconds: 30,
    });
  });

  it('falls back to 60 seconds when interval_seconds is missing or zero', () => {
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval' }).interval_seconds).toBe(60);
    expect(
      buildTriggerUpdatePayload({ id: 'a', type: 'interval', interval_seconds: 0 }).interval_seconds,
    ).toBe(60);
  });

  it('builds datetime payload with a trimmed target', () => {
    expect(buildTriggerUpdatePayload({
      id: 'a',
      type: 'datetime',
      target: '  2026-01-02 03:04:05  ',
      interval_seconds: 10,
      eval_code: 'ignored',
    })).toEqual({
      id: 'a',
      type: 'datetime',
      recall_description: '',
      run_background: false,
      target: '2026-01-02 03:04:05',
    });
  });

  it('builds py-eval payload keeping the code verbatim', () => {
    const code = 'state["n"] > 3\nand True';
    expect(buildTriggerUpdatePayload({
      id: 'a',
      type: 'py-eval',
      eval_code: code,
      target: 'ignored',
    })).toMatchObject({ type: 'py-eval', eval_code: code });
  });

  it('adds lifespan only when it is non-empty', () => {
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: '90' }).lifespan).toBe(90);
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: 0 }).lifespan).toBe(0);
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: '' })).not.toHaveProperty('lifespan');
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: '   ' })).not.toHaveProperty('lifespan');
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: null })).not.toHaveProperty('lifespan');
    expect(buildTriggerUpdatePayload({ id: 'a', type: 'interval', lifespan: undefined })).not.toHaveProperty('lifespan');
  });

  it('does not invent a dynamic field for unknown types (old else-if chain)', () => {
    const payload = buildTriggerUpdatePayload({
      id: 'a',
      type: 'event',
      interval_seconds: 5,
      target: 'x',
      eval_code: 'y',
    });
    expect(payload).toEqual({
      id: 'a',
      type: 'event',
      recall_description: '',
      run_background: false,
    });
  });
});

describe('buildTriggerSubmitPayload', () => {
  it('includes priority and trims id/description but never sends remove_when', () => {
    const payload = buildTriggerSubmitPayload(form({
      id: '  report ',
      recallDescription: ' 日报 ',
      priority: 'interrupt',
      removeWhen: 'after 24 hours',
    }));
    expect(payload).toEqual({
      id: 'report',
      type: 'interval',
      recall_description: '日报',
      run_background: false,
      priority: 'interrupt',
      interval_seconds: 60,
    });
    expect(payload).not.toHaveProperty('remove_when');
  });

  it('keeps the three type branches', () => {
    expect(buildTriggerSubmitPayload(form({ type: 'interval', intervalSeconds: '15' })))
      .toMatchObject({ interval_seconds: 15 });
    expect(buildTriggerSubmitPayload(form({ type: 'datetime', target: ' 2026-05-06 07:08:09 ' })))
      .toMatchObject({ target: '2026-05-06 07:08:09' });
    expect(buildTriggerSubmitPayload(form({ type: 'py-eval', evalCode: 'True' })))
      .toMatchObject({ eval_code: 'True' });
  });

  it('defaults interval to 60 and only sends lifespan when entered', () => {
    expect(buildTriggerSubmitPayload(form({ intervalSeconds: '' })).interval_seconds).toBe(60);
    expect(buildTriggerSubmitPayload(form({ lifespan: ' 120 ' })).lifespan).toBe(120);
    expect(buildTriggerSubmitPayload(form({ lifespan: '   ' }))).not.toHaveProperty('lifespan');
  });

  it('keeps run_background and priority as booleans/strings', () => {
    expect(buildTriggerSubmitPayload(form({ runBackground: true })).run_background).toBe(true);
    expect(buildTriggerSubmitPayload(form({ priority: 'batched' })).priority).toBe('batched');
  });
});
