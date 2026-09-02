"""Inline SVG icons for TenderTools UI."""

CLOUD_UPLOAD = """
<svg class="tt-cloud-icon" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M36 20a8 8 0 0 0-15.5-2.2A6 6 0 1 0 12 28h24a4 4 0 0 0 0-8h-0.5z" fill="#dbeafe" stroke="#3b82f6" stroke-width="1.5"/>
  <path d="M24 18v14M18 26l6-6 6 6" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

PDF_ICON = """
<div class="tt-side-panel tt-side-pdf">
  <p class="tt-hand-label tt-hand-pdf">Your Tender PDF</p>
  <div class="tt-glow tt-glow-pdf">
    <svg class="tt-doc-icon tt-doc-pdf" viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="pdfGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:#fca5a5"/>
          <stop offset="100%" style="stop-color:#dc2626"/>
        </linearGradient>
      </defs>
      <path d="M12 8h58l32 32v92H12V8z" fill="url(#pdfGrad)" stroke="#b91c1c" stroke-width="2"/>
      <path d="M70 8v32h32" fill="#fecaca" stroke="#b91c1c" stroke-width="1.5"/>
      <text x="38" y="88" font-family="Segoe UI,Arial,sans-serif" font-weight="800" font-size="22" fill="#fff">PDF</text>
    </svg>
  </div>
  <svg class="tt-connector tt-connector-pdf" viewBox="0 0 120 60" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path class="tt-connector-path" d="M8 45 Q60 5 112 25" fill="none" stroke="#cbd5e1" stroke-width="2.5" stroke-dasharray="7 6" stroke-linecap="round"/>
    <polygon points="104,19 114,25 104,31" fill="#94a3b8"/>
  </svg>
</div>
"""

XLS_ICON = """
<div class="tt-side-panel tt-side-xls">
  <svg class="tt-connector tt-connector-xls" viewBox="0 0 120 60" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path class="tt-connector-path" d="M112 45 Q60 5 8 25" fill="none" stroke="#cbd5e1" stroke-width="2.5" stroke-dasharray="7 6" stroke-linecap="round"/>
    <polygon points="16,19 6 25 16 31" fill="#94a3b8"/>
  </svg>
  <div class="tt-glow tt-glow-xls">
    <svg class="tt-doc-icon tt-doc-xls" viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="xlsGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:#86efac"/>
          <stop offset="100%" style="stop-color:#16a34a"/>
        </linearGradient>
      </defs>
      <path d="M12 8h58l32 32v92H12V8z" fill="url(#xlsGrad)" stroke="#15803d" stroke-width="2"/>
      <path d="M70 8v32h32" fill="#bbf7d0" stroke="#15803d" stroke-width="1.5"/>
      <line x1="28" y1="52" x2="88" y2="52" stroke="rgba(255,255,255,0.55)" stroke-width="2"/>
      <line x1="28" y1="72" x2="88" y2="72" stroke="rgba(255,255,255,0.55)" stroke-width="2"/>
      <line x1="28" y1="92" x2="88" y2="92" stroke="rgba(255,255,255,0.55)" stroke-width="2"/>
      <line x1="48" y1="48" x2="48" y2="108" stroke="rgba(255,255,255,0.55)" stroke-width="2"/>
      <line x1="68" y1="48" x2="68" y2="108" stroke="rgba(255,255,255,0.55)" stroke-width="2"/>
      <text x="32" y="128" font-family="Segoe UI,Arial,sans-serif" font-weight="800" font-size="18" fill="#fff">XLS</text>
    </svg>
  </div>
  <p class="tt-hand-label tt-hand-xls">Clean Excel Output</p>
</div>
"""

LOGO_ICON = """
<svg class="tt-logo-svg" viewBox="0 0 42 42" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#3b82f6"/>
      <stop offset="100%" style="stop-color:#1d4ed8"/>
    </linearGradient>
  </defs>
  <rect width="42" height="42" rx="10" fill="url(#logoGrad)"/>
  <text x="21" y="28" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-weight="800" font-size="20" fill="#fff">T</text>
</svg>
"""
