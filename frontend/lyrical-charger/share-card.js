/* ============================================================
   Lyrical Charger -- Shareable Charge Card
   Client-side canvas renderer. Draws a 1080x1080 card from a
   calibrate result and hands it to the native share sheet (or a
   download fallback). No backend, no dependency.

   Composition: "deadpan quip is hero" --
     - deadpan_line set large as the statement
     - charge + tier ride as a corner badge
     - tier-colored phosphor glow on the card border (CRT device look)
     - title/artist + topics + Lyrical Charger watermark
   ============================================================ */
(function () {
  'use strict';

  var SIZE = 1080;

  // Canonical RC tier palette (matches main.css :root --rc-*).
  var TIER_HEX = {
    violet: '#aa54ff',
    blue: '#3388ff',
    green: '#33cc55',
    orange: '#ffbb33',
    red: '#ff3333',
  };
  var TIER_LABELS = {
    violet: 'Ascended',
    blue: 'Elevated',
    green: 'Decent',
    orange: 'Degraded',
    red: 'Corrupted',
  };

  // The compass mark, identical to the page header SVG. Rasterized once
  // via an <img> from a data URL so canvas draws it crisply.
  // The compass gauge. The needle rotates to the charge: 0 points straight up
  // (green / Decent, dead center), positive leans left toward violet (Ascended),
  // negative leans right toward red (Corrupted).
  function compassSVG(rotDeg) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">' +
      '<rect width="32" height="32" rx="6" fill="#0a0a14"/>' +
      '<path d="M 5,20 A 11,11 0 0,1 7.6,13.1" fill="none" stroke="#9933ff" stroke-width="6"/>' +
      '<path d="M 7.6,13.1 A 11,11 0 0,1 12.6,9.6" fill="none" stroke="#3388ff" stroke-width="6"/>' +
      '<path d="M 12.6,9.6 A 11,11 0 0,1 19.4,9.6" fill="none" stroke="#33cc55" stroke-width="6"/>' +
      '<path d="M 19.4,9.6 A 11,11 0 0,1 24.4,13.1" fill="none" stroke="#ffbb33" stroke-width="6"/>' +
      '<path d="M 24.4,13.1 A 11,11 0 0,1 27,20" fill="none" stroke="#ff3333" stroke-width="6"/>' +
      '<g transform="rotate(' + rotDeg + ' 16 20)">' +
      '<polygon points="16,8 13.9,20 18.1,20" fill="#eeeef4"/>' +
      '</g>' +
      '<circle cx="16" cy="20" r="3.2" fill="#00d4aa"/>' +
      '</svg>';
  }

  // Map a charge (-100..100) to an SVG needle rotation. Negative degrees rotate
  // counter-clockwise (toward violet/left); positive toward red/right.
  function chargeToRot(charge) {
    var c = Math.max(-100, Math.min(100, charge || 0));
    return -(c / 100) * 78;
  }

  var _compassCache = {};
  function loadCompass(rotDeg) {
    var key = String(Math.round(rotDeg));
    if (_compassCache[key]) return Promise.resolve(_compassCache[key]);
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () { _compassCache[key] = img; resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(compassSVG(rotDeg));
    });
  }

  // Make sure the webfonts we draw with are actually rasterizable before
  // we paint -- canvas silently falls back to a system font otherwise.
  function ensureFonts() {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var wanted = [
      '700 44px "JetBrains Mono"',
      '400 26px "JetBrains Mono"',
      '700 42px "Inter"',
      '600 84px "Inter"',
      '400 30px "Inter"',
    ];
    return Promise.all(wanted.map(function (f) {
      return document.fonts.load(f).catch(function () {});
    })).then(function () { return document.fonts.ready; });
  }

  function hexToRgba(hex, a) {
    var h = hex.replace('#', '');
    var r = parseInt(h.substring(0, 2), 16);
    var g = parseInt(h.substring(2, 4), 16);
    var b = parseInt(h.substring(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  // Greedy word-wrap, then shrink the font until the block fits maxLines.
  function fitText(ctx, text, family, weight, maxWidth, maxLines, startPx, minPx) {
    var size = startPx;
    while (size >= minPx) {
      ctx.font = weight + ' ' + size + 'px ' + family;
      var words = String(text).split(/\s+/).filter(Boolean);
      var lines = [];
      var cur = '';
      for (var i = 0; i < words.length; i++) {
        var trial = cur ? cur + ' ' + words[i] : words[i];
        if (ctx.measureText(trial).width <= maxWidth || !cur) {
          cur = trial;
        } else {
          lines.push(cur);
          cur = words[i];
        }
      }
      if (cur) lines.push(cur);
      if (lines.length <= maxLines) {
        return { lines: lines, size: size, lineHeight: Math.round(size * 1.18) };
      }
      size -= 4;
    }
    // Floor: wrap at the minimum size, allowing as many lines as needed.
    ctx.font = weight + ' ' + minPx + 'px ' + family;
    var fwords = String(text).split(/\s+/).filter(Boolean);
    var flines = [];
    var fcur = '';
    for (var k = 0; k < fwords.length; k++) {
      var ft = fcur ? fcur + ' ' + fwords[k] : fwords[k];
      if (ctx.measureText(ft).width <= maxWidth || !fcur) {
        fcur = ft;
      } else {
        flines.push(fcur);
        fcur = fwords[k];
      }
    }
    if (fcur) flines.push(fcur);
    return { lines: flines, size: minPx, lineHeight: Math.round(minPx * 1.18) };
  }

  function roundRect(ctx, x, y, w, h, r) {
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); return; }
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // Small outlined speech bubble (the comment indicator from the RC badge).
  function drawBubble(ctx, x, y, color) {
    var w = 30, h = 22, r = 7;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2.5;
    roundRect(ctx, x, y, w, h, r);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + 8, y + h - 1);
    ctx.lineTo(x + 5, y + h + 9);
    ctx.lineTo(x + 17, y + h - 1);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function pick(data) {
    var tierKey = (data.tier && TIER_HEX[data.tier]) ? data.tier : 'green';
    var hex = TIER_HEX[tierKey];
    var label = data.tier_label || TIER_LABELS[tierKey] || 'Decent';
    var charge = (data.charge != null) ? data.charge : 0;
    var chargeStr = (charge >= 0 ? '+' : '') + charge;
    var deadpan = (data.deadpan_line && data.deadpan_line.trim()) ? data.deadpan_line.trim() : '';
    var summary = (data.charge_summary && data.charge_summary.trim()) ? data.charge_summary.trim() : '';
    var title = (data.title && data.title !== 'Untitled') ? data.title : '';
    var artist = (data.artist && data.artist !== 'Unknown') ? data.artist : '';
    var topics = Array.isArray(data.topics) ? data.topics.slice(0, 3) : [];
    return { hex: hex, label: label, chargeStr: chargeStr, deadpan: deadpan,
             summary: summary, title: title, artist: artist, topics: topics };
  }

  function setLS(ctx, px) {
    if ('letterSpacing' in ctx) ctx.letterSpacing = px + 'px';
  }

  // Draw the card into `canvas` (sized to 1080). Returns the canvas.
  async function render(data, canvas) {
    await ensureFonts();
    var chargeNum = (data.charge != null) ? data.charge : 0;
    var compass = await loadCompass(chargeToRot(chargeNum)); // needle points at the charge
    var compassFlat = await loadCompass(0);                  // upright mark for the brand tag

    canvas.width = SIZE;
    canvas.height = SIZE;
    var ctx = canvas.getContext('2d');
    var v = pick(data);
    var P = 100;   // top / bottom padding
    var PX = 110;  // left / right padding (+10%)

    // --- Background: deep vertical gradient
    var bg = ctx.createLinearGradient(0, 0, 0, SIZE);
    bg.addColorStop(0, '#0e0e1a');
    bg.addColorStop(0.55, '#0a0a14');
    bg.addColorStop(1, '#060609');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, SIZE, SIZE);

    // Faint tier glow blooming from behind the hero
    var rg = ctx.createRadialGradient(SIZE / 2, SIZE * 0.46, 40, SIZE / 2, SIZE * 0.46, SIZE * 0.62);
    rg.addColorStop(0, hexToRgba(v.hex, 0.18));
    rg.addColorStop(1, hexToRgba(v.hex, 0));
    ctx.fillStyle = rg;
    ctx.fillRect(0, 0, SIZE, SIZE);

    // --- CRT scanlines (subtle horizontal banding)
    ctx.fillStyle = 'rgba(0,0,0,0.05)';
    for (var sy = 0; sy < SIZE; sy += 4) ctx.fillRect(0, sy, SIZE, 2);

    // --- Vignette for CRT depth
    var vg = ctx.createRadialGradient(SIZE / 2, SIZE / 2, SIZE * 0.34, SIZE / 2, SIZE / 2, SIZE * 0.72);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.45)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, SIZE, SIZE);

    // --- Square card border with tier phosphor glow (INNER glow only, cranked up)
    var bx = 30, bw = SIZE - 60, blw = 7; // 0 radius; thicker border
    // Inner glow: clip to the card interior so the bloom falls only inward.
    ctx.save();
    ctx.beginPath();
    ctx.rect(bx, bx, bw, bw);
    ctx.clip();
    ctx.shadowColor = v.hex;
    ctx.shadowBlur = 66; // tighter spread; passes below keep the strength
    ctx.strokeStyle = v.hex;
    ctx.lineWidth = blw;
    ctx.strokeRect(bx, bx, bw, bw);
    ctx.strokeRect(bx, bx, bw, bw);
    ctx.strokeRect(bx, bx, bw, bw); // extra passes crank the inner bloom
    ctx.restore();
    // Crisp square border edge on top (no shadow, full width)
    ctx.shadowBlur = 0;
    ctx.strokeStyle = v.hex;
    ctx.lineWidth = blw;
    ctx.strokeRect(bx, bx, bw, bw);

    // ===== HERO (top, left-aligned): title, deadpan, artist, charge -- equidistant rows =====
    var leftX = PX;
    var contentW = SIZE - PX * 2;
    var G = 46; // equidistant gap between hero rows
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';

    // ----- Badge column (right 1/5): big compass gauge + tier label + charge, stacked -----
    var tile = 138;
    var tileX = SIZE - PX - tile;
    var tileY = P;
    var badgeCx = tileX + tile / 2;
    if (compass) ctx.drawImage(compass, tileX, tileY, tile, tile);
    ctx.textAlign = 'center';
    ctx.fillStyle = v.hex;
    ctx.font = '600 30px "Inter"';
    ctx.fillText(v.label, badgeCx, tileY + tile + 38);
    ctx.fillStyle = '#f4f4fa';
    ctx.font = '700 50px "JetBrains Mono"';
    ctx.fillText(v.chargeStr, badgeCx, tileY + tile + 92);
    var badgeBottom = tileY + tile + 92;
    ctx.textAlign = 'left';

    // ----- Left 4/5 column: title, artist, deadpan -- stacked from the top -----
    var colW = tileX - leftX - 30;
    var tFit = fitText(ctx, v.title || 'Untitled', '"Inter"', '700', colW, 4, 100, 52);
    var ty = P + tFit.size;
    ctx.fillStyle = '#f4f4fa';
    ctx.font = '700 ' + tFit.size + 'px "Inter"';
    for (var ti = 0; ti < tFit.lines.length; ti++) {
      ctx.fillText(tFit.lines[ti], leftX, ty + ti * tFit.lineHeight);
    }
    var y = ty + (tFit.lines.length - 1) * tFit.lineHeight;

    // Artist -- nudged up toward the title. We keep the cascade slot (y) so the
    // deadpan/summary below do not move; only the drawn baseline shifts up.
    if (v.artist) {
      y += G + 46;
      ctx.fillStyle = '#9a9ab0';
      ctx.font = '400 46px "Inter"';
      ctx.fillText(v.artist, leftX, y - 28);
    }

    // Deadpan -- below the artist, within the 4/5 column (10% smaller)
    if (v.deadpan) {
      var dSize = Math.round(((tFit.size + 40) / 2) * 0.81);
      var dFit = fitText(ctx, v.deadpan, '"Inter"', '600', colW, 3, dSize, 36);
      y += G + dFit.size;
      ctx.fillStyle = '#e8e8f0';
      ctx.font = '600 ' + dFit.size + 'px "Inter"';
      for (var di = 0; di < dFit.lines.length; di++) {
        ctx.fillText(dFit.lines[di], leftX, y + di * dFit.lineHeight);
      }
      y += (dFit.lines.length - 1) * dFit.lineHeight;
    }

    // ----- Summary: full width (1/1), below both columns -----
    // With no deadpan the summary carries the card, so render it ~2x larger.
    if (v.summary) {
      var y2 = Math.max(y, badgeBottom);
      var sFit = v.deadpan
        ? fitText(ctx, v.summary, '"Inter"', '400', contentW, 3, 32, 26)
        : fitText(ctx, v.summary, '"Inter"', '400', contentW, 6, 54, 38);
      y2 += 44 + sFit.size;
      ctx.fillStyle = '#b8b8c6';
      ctx.font = '400 ' + sFit.size + 'px "Inter"';
      for (var si = 0; si < sFit.lines.length; si++) {
        ctx.fillText(sFit.lines[si], leftX, y2 + si * sFit.lineHeight);
      }
    }

    // ===== BOTTOM (left-aligned, extra padding from the base) =====
    // Topics
    if (v.topics.length) {
      ctx.fillStyle = hexToRgba(v.hex, 0.9);
      ctx.font = '400 26px "JetBrains Mono"';
      var tline = v.topics.map(function (t) { return '#' + String(t).replace(/^#/, ''); }).join('   ');
      ctx.fillText(tline, leftX, SIZE - P - 114);
    }

    // Brand tagline: LYRICAL CHARGER, powered by the RISING COMPASS.
    // Both brand names share one style; the connective is the dim link.
    var fy = SIZE - P - 44;
    var markSize = 32;
    if (compassFlat) ctx.drawImage(compassFlat, leftX, fy - markSize / 2, markSize, markSize);
    ctx.textBaseline = 'middle';
    var tx = leftX + markSize + 14;
    ctx.fillStyle = '#c8c8d8';
    ctx.font = '700 22px "JetBrains Mono"';
    setLS(ctx, 2);
    ctx.fillText('LYRICAL CHARGER', tx, fy + 1);
    tx += ctx.measureText('LYRICAL CHARGER').width + 6;
    setLS(ctx, 0);
    ctx.fillStyle = '#6a6a82';
    ctx.font = '400 18px "JetBrains Mono"';
    ctx.fillText(', powered by the ', tx, fy + 1);
    tx += ctx.measureText(', powered by the ').width + 4;
    ctx.fillStyle = '#c8c8d8';
    ctx.font = '700 22px "JetBrains Mono"';
    setLS(ctx, 2);
    ctx.fillText('RISING COMPASS', tx, fy + 1);
    setLS(ctx, 0);
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#6a6a82';
    ctx.font = '400 20px "JetBrains Mono"';
    ctx.fillText('risingcompass.net/lyrical-charger', leftX, SIZE - P);

    return canvas;
  }

  function canvasToBlob(canvas) {
    return new Promise(function (resolve) {
      canvas.toBlob(function (b) { resolve(b); }, 'image/png');
    });
  }

  function slugify(s) {
    return String(s || 'reading').toLowerCase().replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '').slice(0, 60) || 'reading';
  }

  // Share via the native sheet when possible (mobile -> Instagram/Stories);
  // otherwise download the PNG.
  async function shareOrDownload(canvas, data, forceDownload) {
    var v = pick(data);
    var blob = await canvasToBlob(canvas);
    var fname = 'lyrical-charge-' + slugify(data.title || v.label) + '.png';
    var text = (data.title ? '"' + data.title + '" ' : '') + 'charged ' + v.chargeStr +
      ' (' + (data.tier_label || v.label) + ') on the Lyrical Charger.';
    var shareUrl = 'https://risingcompass.net/lyrical-charger/';

    if (!forceDownload) {
      try {
        if (navigator.canShare && window.File) {
          var file = new File([blob], fname, { type: 'image/png' });
          if (navigator.canShare({ files: [file] })) {
            await navigator.share({ files: [file], text: text, url: shareUrl });
            return 'shared';
          }
        }
      } catch (e) {
        if (e && e.name === 'AbortError') return 'cancelled';
        /* fall through to download */
      }
    }
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    return 'downloaded';
  }

  window.LCShareCard = { render: render, shareOrDownload: shareOrDownload, _pick: pick };
})();
