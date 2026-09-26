export type MeasurementSystem = 'us' | 'metric';
export type MeasurementField = 'feet' | 'inches' | 'pounds' | 'centimeters' | 'kilograms';
export type MeasurementDraft = {
  system: MeasurementSystem;
  heightCm?: number;
  weightKg?: number;
  fields: Record<MeasurementField, string>;
};
const CM_PER_INCH = 2.54;
const KG_PER_POUND = 0.45359237;
const display = (value: number | undefined) => value === undefined || !Number.isFinite(value) ? '' : String(Math.round(value * 10) / 10);
const number = (value: string) => value.trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : undefined;

function fieldsFor(heightCm?: number, weightKg?: number): MeasurementDraft['fields'] {
  // Round the total first so 11.96 inches carries into the next foot.
  const tenths = heightCm === undefined ? undefined : Math.round(heightCm / CM_PER_INCH * 10);
  return {
    feet: tenths === undefined ? '' : String(Math.floor(tenths / 120)),
    inches: tenths === undefined ? '' : String((tenths % 120) / 10),
    pounds: display(weightKg === undefined ? undefined : weightKg / KG_PER_POUND),
    centimeters: display(heightCm), kilograms: display(weightKg),
  };
}
export function createMeasurements(heightCm?: number, weightKg?: number, system: MeasurementSystem = 'us'): MeasurementDraft {
  return { system, heightCm, weightKg, fields: fieldsFor(heightCm, weightKg) };
}
export function switchMeasurementSystem(draft: MeasurementDraft, system: MeasurementSystem): MeasurementDraft {
  if (system === draft.system) return draft;
  // Toggling changes presentation only; rounded fields never overwrite canonical values.
  return { ...draft, system, fields: fieldsFor(draft.heightCm, draft.weightKg) };
}
export function editMeasurement(draft: MeasurementDraft, field: MeasurementField, value: string): MeasurementDraft {
  const fields = { ...draft.fields, [field]: value };
  let { heightCm, weightKg } = draft;
  if (field === 'centimeters') heightCm = number(value);
  if (field === 'kilograms') weightKg = number(value);
  if (field === 'pounds') { const pounds = number(value); weightKg = pounds === undefined ? undefined : pounds * KG_PER_POUND; }
  if (field === 'feet' || field === 'inches') {
    const feet = number(fields.feet);
    const inches = fields.inches.trim() === '' ? 0 : number(fields.inches);
    heightCm = feet !== undefined && Number.isInteger(feet) && feet >= 0 && inches !== undefined && inches >= 0 && inches < 12 ? (feet * 12 + inches) * CM_PER_INCH : undefined;
  }
  return { ...draft, fields, heightCm, weightKg };
}
export function measurementsValid(draft: Pick<MeasurementDraft, 'heightCm' | 'weightKg'>): boolean {
  return draft.heightCm !== undefined && Number.isFinite(draft.heightCm) && draft.heightCm >= 80 && draft.heightCm <= 250 && draft.weightKg !== undefined && Number.isFinite(draft.weightKg) && draft.weightKg >= 25 && draft.weightKg <= 300;
}
export function formatHeight(heightCm?: number, system: MeasurementSystem = 'us'): string {
  if (heightCm === undefined) return '—';
  const fields = fieldsFor(heightCm);
  return system === 'us' ? `${fields.feet} ft ${fields.inches} in` : `${fields.centimeters} cm`;
}
export function formatWeight(weightKg?: number, system: MeasurementSystem = 'us'): string {
  if (weightKg === undefined) return '—';
  const fields = fieldsFor(undefined, weightKg);
  return system === 'us' ? `${fields.pounds} lb` : `${fields.kilograms} kg`;
}
