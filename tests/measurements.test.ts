import { describe, expect, it } from 'vitest';
import { createMeasurements, editMeasurement, formatHeight, formatWeight, measurementsValid, switchMeasurementSystem } from '../web/src/lib/measurements';

describe('Measurement input and presentation', () => {
  it('defaults to US units with empty quantities', () => {
    const draft = createMeasurements();
    expect(draft.system).toBe('us'); expect(draft.fields.feet).toBe(''); expect(measurementsValid(draft)).toBe(false);
  });
  it('normalizes feet/inches and pounds with exact conversion constants', () => {
    let draft = editMeasurement(createMeasurements(), 'feet', '5');
    draft = editMeasurement(draft, 'inches', '10'); draft = editMeasurement(draft, 'pounds', '150');
    expect(draft.heightCm).toBeCloseTo(177.8, 10); expect(draft.weightKg).toBeCloseTo(68.0388555, 10);
    expect(measurementsValid(draft)).toBe(true);
  });
  it('preserves canonical values through repeated rounded display toggles', () => {
    let draft = createMeasurements(177.837, 68.0388555, 'metric');
    for (let i = 0; i < 100; i++) draft = switchMeasurementSystem(draft, i % 2 ? 'metric' : 'us');
    expect(draft.heightCm).toBe(177.837); expect(draft.weightKg).toBe(68.0388555);
  });
  it('does not change height when only weight is edited after a toggle', () => {
    const draft = editMeasurement(switchMeasurementSystem(createMeasurements(177.837, 68, 'metric'), 'us'), 'pounds', '160');
    expect(draft.heightCm).toBe(177.837); expect(draft.weightKg).toBeCloseTo(72.5747792, 10);
  });
  it('carries rounded inches into feet instead of displaying twelve inches', () => {
    expect(formatHeight(182.8294, 'us')).toBe('6 ft 0 in');
    expect(formatHeight(175, 'metric')).toBe('175 cm'); expect(formatWeight(70, 'us')).toBe('154.3 lb');
  });
  it.each(['12', '-1', 'Infinity', 'not a number'])('rejects invalid inches %s', inches => {
    const draft = editMeasurement(createMeasurements(175, 70), 'inches', inches);
    expect(measurementsValid(draft)).toBe(false);
  });
  it('rejects fractional feet and accepts omitted inches as zero', () => {
    expect(measurementsValid(editMeasurement(createMeasurements(175, 70), 'feet', '5.5'))).toBe(false);
    let draft = editMeasurement(createMeasurements(undefined, 70), 'feet', '5');
    draft = editMeasurement(draft, 'inches', ''); expect(draft.heightCm).toBe(152.4);
  });
  it.each([[80, 25, true], [250, 300, true], [79.9, 70, false], [251, 70, false], [175, 24, false], [175, 301, false]])('validates canonical bounds %s/%s', (height, weight, valid) => {
    expect(measurementsValid(createMeasurements(height as number, weight as number))).toBe(valid);
  });
});
