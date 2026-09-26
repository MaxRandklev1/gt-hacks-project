export const PHOTO_COUNT = 8;
export const CONSENT_VERSION = 'identity-training-v1';
export function validateReferenceSelfie(selfie: Pick<File, 'size' | 'type'> | null | undefined, capturedAt: number, now = Date.now()) {
  if (!selfie || selfie.type !== 'image/jpeg' || selfie.size <= 0 || selfie.size > 20 * 1024 * 1024) {
    throw new Error('Take a clear selfie with your camera before continuing.');
  }
  if (!Number.isFinite(capturedAt) || capturedAt < now - 60 * 60 * 1000 || capturedAt > now + 2 * 60 * 1000) {
    throw new Error('Take a new selfie so your reference shows your current look.');
  }
}
export function validateMeasurements(heightCm: number, weightKg: number) {
  if (!Number.isFinite(heightCm) || heightCm < 80 || heightCm > 250) throw new Error('Enter a height between 80 and 250 cm.');
  if (!Number.isFinite(weightKg) || weightKg < 25 || weightKg > 300) throw new Error('Enter a weight between 25 and 300 kg.');
}
export function validatePhotoFiles(photos: Pick<File, 'size' | 'type' | 'name'>[]) {
  if (photos.length !== PHOTO_COUNT) throw new Error('Choose exactly eight photos of yourself.');
  for (const photo of photos) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(photo.type)) throw new Error(`${photo.name}: use JPG, PNG, or WebP. Export HEIC photos as JPG first.`);
    if (photo.size > 20 * 1024 * 1024 || photo.size === 0) throw new Error(`${photo.name}: each photo must be between 1 byte and 20 MB.`);
  }
}
