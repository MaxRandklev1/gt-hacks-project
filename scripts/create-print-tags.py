"""Build the final two-page US Letter tag sheet from the pinned printed-tag manifest.

Requires Python reportlab and the repository's npm dependencies (qrcode).
Run after `pnpm qr:demo`; this draws the same QR modules as the SVG assets.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_NAME = "thread-tags-print.pdf"


def main():
    manifest = json.loads((ROOT / "docs/tags/printed-threads.json").read_text(encoding="utf-8"))
    garments = manifest["garments"]
    if [item["id"] for item in garments] != [f"thread-{i}" for i in range(1, 11)]:
        raise ValueError("The final print sheet requires the ten reserved thread IDs in order.")
    for item in garments:
        if item["url"] != f"https://gt-hacks-thread-2026.firebaseapp.com/g/{item['id']}":
            raise ValueError("A printed destination changed; keep the original URL.")
        for directory in ("docs/tags", "web/public/tags"):
            data = (ROOT / directory / f"{item['id']}.svg").read_bytes()
            if hashlib.sha256(data).hexdigest() != item["svgSha256"]:
                raise ValueError(f"The pinned SVG for {item['id']} does not match {directory}.")

    # Pass URLs as JSON on stdin, never as shell command text. Both PDF and SVG
    # use the same pinned qrcode package, error correction M and four-module border.
    code = """
import QRCode from 'qrcode';
import fs from 'node:fs';
const urls = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(urls.map(url => {
  const qr = QRCode.create(url, {errorCorrectionLevel: 'M'});
  return {size: qr.modules.size, data: Array.from(qr.modules.data)};
})));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code], cwd=ROOT,
        input=json.dumps([item["url"] for item in garments]),
        text=True, capture_output=True, check=True,
    )
    matrices = json.loads(result.stdout)
    output = ROOT / "docs/tags" / OUTPUT_NAME
    pdf = canvas.Canvas(str(output), pagesize=letter, invariant=1, pageCompression=1)
    pdf.setTitle("THREAD 1-10 - Final printed clothing tags")
    pdf.setAuthor("THREAD")
    pdf.setSubject("Fixed garment URLs. Print at actual size and keep the white QR border.")
    page_width, page_height = letter
    for page in range(2):
        pdf.setFillColor(colors.HexColor("#202720"))
        pdf.setFont("Helvetica-Bold", 17)
        pdf.drawString(30, page_height - 31, "THREAD / PRINTED TAGS")
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(30, page_height - 48, "Print at actual size (100%). Cut on the dotted lines. Keep each white QR border.")
        pdf.drawRightString(page_width - 30, page_height - 31, f"{page + 1} / 2")
        for slot, index in enumerate(range(page * 6, min(page * 6 + 6, len(garments)))):
            item, matrix = garments[index], matrices[index]
            col, row = slot % 2, slot // 2
            x, y, width, height = 30 + col * 282, 508 - row * 226, 270, 212
            center = x + width / 2
            pdf.setStrokeColor(colors.HexColor("#aaaaaa"))
            pdf.setLineWidth(0.5)
            pdf.setDash(2, 3)
            pdf.rect(x, y, width, height, fill=0, stroke=1)
            pdf.setDash()
            pdf.setFillColor(colors.black)
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawCentredString(center, y + height - 22, item["label"])
            pdf.setFont("Helvetica", 8.5)
            description = item["description"].replace("\u2013", "-").replace("\u2014", "-")
            if pdf.stringWidth(description, "Helvetica", 8.5) > width - 24:
                raise ValueError(f"Description is too wide for {item['id']}; shorten its caption.")
            pdf.drawCentredString(center, y + height - 36, description)
            qr_width = 144  # A two-inch vector QR, including its quiet zone.
            qr_x, qr_y = center - qr_width / 2, y + 25
            size, data = matrix["size"], matrix["data"]
            pitch = qr_width / (size + 8)
            pdf.setFillColor(colors.white)
            pdf.rect(qr_x, qr_y, qr_width, qr_width, stroke=0, fill=1)
            pdf.setFillColor(colors.black)
            for r in range(size):
                c = 0
                while c < size:
                    if not data[r * size + c]:
                        c += 1
                        continue
                    end = c + 1
                    while end < size and data[r * size + end]:
                        end += 1
                    pdf.rect(qr_x + (4 + c) * pitch,
                             qr_y + (4 + size - 1 - r) * pitch,
                             (end - c) * pitch, pitch, stroke=0, fill=1)
                    c = end
            pdf.linkURL(item["url"], (qr_x, qr_y, qr_x + qr_width, qr_y + qr_width), relative=0)
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(center, y + 13, f"Scan to see it on you  /  {item['id']}")
        pdf.setFont("Helvetica", 7.5)
        pdf.setFillColor(colors.HexColor("#555555"))
        pdf.drawString(30, 25, "Fixed links: https://gt-hacks-thread-2026.firebaseapp.com/g/thread-1 through thread-10")
        pdf.showPage()
    pdf.save()
    published = ROOT / "web/public/tags" / OUTPUT_NAME
    published.write_bytes(output.read_bytes())
    print(json.dumps({"pdf": str(output), "pages": 2, "tags": len(garments),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
