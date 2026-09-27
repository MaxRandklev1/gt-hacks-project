# Thread

**A personal fitting room, one scan away.**

## Inspiration

I want to get more into fashion and put more thought into how I present myself, but trying on clothes can be a hassle, especially when fitting rooms are closed. I've bought desert-tan shirts that blended into my skin tone so much they barely looked like shirts. I built Thread to make that "does this actually look good on me?" decision easier before buying.

## What it does

Sign in with Google, enter your height, weight and preferred body style, and take or upload one selfie. Thread creates your personal look, then lets you scan a garment's QR tag or browse the collection to see yourself wearing it. You can revisit past looks, like or dislike pieces, save a wish list and download your images. Body templates provide an approximate visual preview.

## How I built it

- **Web app:** React, TypeScript and Vite power the mobile-friendly interface, camera capture and QR scanner.
- **Cloud backend:** Firebase Hosting, Authentication, Firestore and private Storage handle sign-in, profiles, job progress, saved looks and images. A Python worker connects the hosted app to ComfyUI running on a local RTX 5080 GPU.
- **Personalization:** Qwen Image 2.1 creates a reusable personal base from the selfie. Height, weight and body style select the matching pose template; identity, body pose and clothing references have separate roles.
- **Fast try-ons:** Garments are rendered once per body template and cached across users. SegFormer human parsing through ONNX supplies masks, and NumPy/OpenCV compositing layers clothing onto the personal base while retaining hair and head coverings. Cached try-ons use a separate CPU lane and save both 1024-pixel originals and 2K images.

## Challenges I faced

The hardest balance was likeness versus speed. I initially trained a personal LoRA and regenerated each outfit, adding minutes to the experience. Reusing personal bases and garment renders brought subsequent try-ons to roughly 1–2 seconds of local processing, with network and queue time additional. Keeping poses aligned, preserving garment artwork and blending clothing around hair and skin required careful masking and compositing.

## What I learned

I learned that the biggest improvement came from changing when the expensive work happened. Generating reusable assets ahead of time made a complex image pipeline practical for repeated interactions. Simplifying onboarding to one selfie also showed me how much the experience depends on reducing effort for the person using it.

[Try Thread](https://gt-hacks-thread-2026.firebaseapp.com)
