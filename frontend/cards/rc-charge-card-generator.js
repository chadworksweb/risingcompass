/* ============================================================
   Rising Compass -- Charge Card Generator (MASTER)
   (rc-charge-card-generator)

   The single card generator for the whole site: the public song-page / Lyrical
   Charger share card AND the social broadcaster's cards. Exposes
   window.RCChargeCard. No backend, no dependency. (This consolidates the old
   public 1:1 generator and the broadcast 3:4 fork into one source of truth.)

   Aspect ratio is selectable per render via opts.ratio:
     - 'square'   -> 1080x1080 (1:1)
     - 'portrait' -> 1080x1440 (3:4, Instagram profile-grid crop)
   opts.height (a number) overrides both. Default is 'portrait'. Public callers
   pass 'square' for the classic 1:1 card; the ratio toggle on the song / Lyrical
   Charger pages flips it. The phosphor border sits flush to the image edge.
   ============================================================ */
(function () {
  'use strict';

  var SIZE = 1080;       // card WIDTH (the canvas is always SIZE wide)
  var POST_H = 1440;     // 3:4 portrait height -- matches the IG profile-grid thumb

  // Resolve the card height: explicit opts.height wins, else opts.ratio
  // ('square' = 1:1, 'portrait' = 3:4), else the 3:4 post height.
  function cardHeight(opts) {
    var h = opts && opts.height;
    if (typeof h === 'number' && h > 0) return h;
    if (opts && opts.ratio === 'square') return SIZE;
    if (opts && opts.ratio === 'portrait') return POST_H;
    return POST_H;
  }

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
      // Five tier bands sized to the true tier geometry (BOUNDS 0/22.5/67.5/
      // 112.5/157.5/180 in dial degrees; here theta = 180 - degree on a r=11
      // arc centered at (16,20)). Ascended/Corrupted are the narrow 22.5deg
      // poles, middle three 45deg. Mirrors js/compass.js + charge_calc.py.
      '<path d="M 5,20 A 11,11 0 0,1 5.84,15.79" fill="none" stroke="#9933ff" stroke-width="6"/>' +
      '<path d="M 5.84,15.79 A 11,11 0 0,1 11.79,9.84" fill="none" stroke="#3388ff" stroke-width="6"/>' +
      '<path d="M 11.79,9.84 A 11,11 0 0,1 20.21,9.84" fill="none" stroke="#33cc55" stroke-width="6"/>' +
      '<path d="M 20.21,9.84 A 11,11 0 0,1 26.16,15.79" fill="none" stroke="#ffbb33" stroke-width="6"/>' +
      '<path d="M 26.16,15.79 A 11,11 0 0,1 27,20" fill="none" stroke="#ff3333" stroke-width="6"/>' +
      '<g transform="rotate(' + rotDeg + ' 16 20)">' +
      '<polygon points="16,8 13.9,20 18.1,20" fill="#eeeef4"/>' +
      '</g>' +
      '<circle cx="16" cy="20" r="3.2" fill="#00d4aa"/>' +
      '</svg>';
  }

  // Map a charge (-100..100) to an SVG needle rotation. Negative degrees rotate
  // counter-clockwise (toward violet/left); positive toward red/right. The 90deg
  // span matches the site dial (rotation = -score*90/100), so the needle lands in
  // the same reproportioned band as the live compass instead of a compressed arc.
  function chargeToRot(charge) {
    var c = Math.max(-100, Math.min(100, charge || 0));
    return -(c / 100) * 90;
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

  // Relative luminance, to pick dark/light text that stays legible on a tier fill.
  function relLum(hex) {
    var h = hex.replace('#', '');
    var r = parseInt(h.substring(0, 2), 16) / 255;
    var g = parseInt(h.substring(2, 4), 16) / 255;
    var b = parseInt(h.substring(4, 6), 16) / 255;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  function textOn(hex) { return relLum(hex) > 0.55 ? '#0a0a14' : '#f4f4fa'; }

  // The tier-colored charge box: the charge score (where the compass gauge used
  // to be) over the tier label, on a tier-colored fill. Shared by the song card
  // and the reading card. rightEdgeX = right inner edge; topY = top. opts tunes
  // the score/label sizes and padding; opts.anchorBottomY positions the box by
  // its bottom instead of its top (so it can hug the footer band). Returns the
  // box geometry so the body can lay out around it.
  function drawBadge(ctx, hex, label, scoreStr, rightEdgeX, topY, opts) {
    opts = opts || {};
    var scoreSize = opts.scoreSize || 64;
    var labelSize = opts.labelSize || 30;
    var padX = (opts.padX != null) ? opts.padX : 28;
    var padTop = (opts.padTop != null) ? opts.padTop : 20;
    var gap = (opts.gap != null) ? opts.gap : 14;
    var padBottom = (opts.padBottom != null) ? opts.padBottom : 22;
    ctx.font = '700 ' + scoreSize + 'px "JetBrains Mono"';
    var sw = ctx.measureText(scoreStr).width;
    ctx.font = '600 ' + labelSize + 'px "Inter"';
    var lw = ctx.measureText(label).width;
    var boxW = Math.max(opts.minW || 168, Math.max(sw, lw) + padX * 2);
    var boxH = padTop + scoreSize + gap + labelSize + padBottom;
    var boxX = rightEdgeX - boxW;
    var boxY = (opts.anchorBottomY != null) ? (opts.anchorBottomY - boxH) : topY;
    ctx.save();
    roundRect(ctx, boxX, boxY, boxW, boxH, 16);
    ctx.shadowColor = hex;
    ctx.shadowBlur = 30;
    ctx.fillStyle = hex;
    ctx.fill();
    ctx.restore();
    var fg = textOn(hex);
    var cx = boxX + boxW / 2;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = fg;
    ctx.font = '700 ' + scoreSize + 'px "JetBrains Mono"';
    ctx.fillText(scoreStr, cx, boxY + padTop + scoreSize * 0.82);
    ctx.font = '600 ' + labelSize + 'px "Inter"';
    ctx.fillText(label, cx, boxY + padTop + scoreSize + gap + labelSize * 0.82);
    ctx.textAlign = 'left';
    return { left: boxX, right: rightEdgeX, top: boxY, bottom: boxY + boxH, width: boxW, height: boxH };
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
  // opts.brand: 'lyrical-charger' (default) draws the Lyrical Charger wordmark;
  // 'compass' draws just THE RISING COMPASS (used on song pages, where the card
  // is the compass's own and the Lyrical Charger verbiage doesn't belong).
  async function render(data, canvas, opts) {
    var brand = (opts && opts.brand) || 'lyrical-charger';
    await ensureFonts();
    var compassFlat = await loadCompass(0);                  // upright mark for the brand tag

    var H = cardHeight(opts);
    canvas.width = SIZE;
    canvas.height = H;
    var ctx = canvas.getContext('2d');
    var v = pick(data);
    var P = 100;   // top / bottom padding
    var PX = 110;  // left / right padding (+10%)

    // Shared CRT chrome (background, tier bloom behind the hero, scanlines,
    // vignette, rectangular phosphor border) -- same routine the reading card uses.
    drawChrome(ctx, v.hex, H * 0.46, H);

    // ===== HERO (top, left-aligned): title, deadpan, artist, charge -- equidistant rows =====
    var leftX = PX;
    var contentW = SIZE - PX * 2;
    var G = 46; // equidistant gap between hero rows
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';

    // ----- Badge (top-right): tier-colored box, charge score over the tier
    // label, sized up so the charge reads as the dominant element. Single image
    // (no IG carousel icon), so the top-right corner is fine here. -----
    // Drop the title/charge row to the same start the reading card uses for its
    // charge row (kicker height + gap), so the two cards share a vertical rhythm.
    var rowTop = P + 99;
    var badge = drawBadge(ctx, v.hex, v.label, v.chargeStr, SIZE - PX, rowTop,
      { scoreSize: 92, labelSize: 30, minW: 200, padX: 30 });
    var tileX = badge.left;
    var badgeBottom = badge.bottom;

    // ----- Left 4/5 column: title, artist, deadpan -- stacked from the top -----
    var colW = tileX - leftX - 30;
    // The title sits level with the badge, so it gets extra right padding (a
    // wider gap than the column below) to force a line break before it ever
    // butts up against the compass gauge.
    var titleColW = tileX - leftX - 78;
    // Length-aware title sizing: longer titles start smaller so they never
    // dominate the hero. We cap the starting size by character count, then
    // fitText shrinks further to fit the column width within maxLines.
    var titleText = v.title || 'Untitled';
    var titleStart = 100;
    if (titleText.length > 22) {
      titleStart = Math.max(52, Math.round(100 - (titleText.length - 22) * 1.6));
    }
    var tFit = fitText(ctx, titleText, '"Inter"', '700', titleColW, 3, titleStart, 44);
    var ty = rowTop + tFit.size;
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

    // Push the deadpan + summary down, away from the title/artist, so the body
    // sits lower and reads with more breathing room.
    y += 56;

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
        ? fitText(ctx, v.summary, '"Inter"', '400', contentW, 4, 44, 32)
        : fitText(ctx, v.summary, '"Inter"', '400', contentW, 6, 54, 38);
      y2 += 44 + sFit.size;
      ctx.fillStyle = '#b8b8c6';
      ctx.font = '400 ' + sFit.size + 'px "Inter"';
      for (var si = 0; si < sFit.lines.length; si++) {
        ctx.fillText(sFit.lines[si], leftX, y2 + si * sFit.lineHeight);
      }
    }

    // ===== FOOTER (centered, enlarged) =====
    // #topics / wordmark / url, all centered. Matches the reading card's footer.
    var fy = H - P - 70;

    // Topics -- centered above the brand.
    if (v.topics.length) {
      ctx.fillStyle = hexToRgba(v.hex, 0.9);
      ctx.font = '400 28px "JetBrains Mono"';
      ctx.textAlign = 'center';
      var tline = v.topics.map(function (t) { return '#' + String(t).replace(/^#/, ''); }).join('   ');
      ctx.fillText(tline, SIZE / 2, fy - 58);
      ctx.textAlign = 'left';
    }

    // Brand wordmark, centered + enlarged. compass: THE RISING COMPASS.
    var markSize = 42;
    ctx.textBaseline = 'middle';
    if (brand === 'compass') {
      ctx.font = '700 32px "JetBrains Mono"';
      setLS(ctx, 2);
      var wm = 'THE RISING COMPASS';
      var wmW = ctx.measureText(wm).width;
      var startX = (SIZE - (markSize + 16 + wmW)) / 2;
      if (compassFlat) ctx.drawImage(compassFlat, startX, fy - markSize / 2, markSize, markSize);
      ctx.textAlign = 'left';
      ctx.fillStyle = '#c8c8d8';
      ctx.fillText(wm, startX + markSize + 16, fy + 1);
      setLS(ctx, 0);
    } else {
      // LYRICAL CHARGER, powered by the RISING COMPASS -- measured + centered.
      ctx.font = '700 26px "JetBrains Mono"'; setLS(ctx, 2);
      var p1 = 'LYRICAL CHARGER', w1 = ctx.measureText(p1).width;
      setLS(ctx, 0); ctx.font = '400 20px "JetBrains Mono"';
      var p2 = ', powered by the ', w2 = ctx.measureText(p2).width;
      ctx.font = '700 26px "JetBrains Mono"'; setLS(ctx, 2);
      var p3 = 'RISING COMPASS', w3 = ctx.measureText(p3).width;
      setLS(ctx, 0);
      var sX = (SIZE - (markSize + 16 + w1 + 6 + w2 + 4 + w3)) / 2;
      if (compassFlat) ctx.drawImage(compassFlat, sX, fy - markSize / 2, markSize, markSize);
      var cx2 = sX + markSize + 16;
      ctx.textAlign = 'left';
      ctx.fillStyle = '#c8c8d8'; ctx.font = '700 26px "JetBrains Mono"'; setLS(ctx, 2);
      ctx.fillText(p1, cx2, fy + 1); cx2 += w1 + 6; setLS(ctx, 0);
      ctx.fillStyle = '#6a6a82'; ctx.font = '400 20px "JetBrains Mono"';
      ctx.fillText(p2, cx2, fy + 1); cx2 += w2 + 4;
      ctx.fillStyle = '#c8c8d8'; ctx.font = '700 26px "JetBrains Mono"'; setLS(ctx, 2);
      ctx.fillText(p3, cx2, fy + 1); setLS(ctx, 0);
    }
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#6a6a82';
    ctx.font = '400 26px "JetBrains Mono"';
    ctx.fillText(brand === 'compass' ? 'risingcompass.net' : 'risingcompass.net/lyrical-charger',
      SIZE / 2, fy + 48);
    ctx.textAlign = 'left';

    return canvas;
  }

  // ============================================================
  // Daily-aggregate (reading) card -- the day's whole reading on one
  // 1080x1080 card, sibling to the per-song render() above. Same chrome
  // and badge so a feed of per-song + daily cards reads as one set; the
  // body swaps the single-song hero for the date kicker, the editorial as
  // the statement, and the top-5 song list. Drives Build 6's daily-aggregate
  // social post (the Playwright harness screenshots this exactly like the
  // per-song card). Input = the normalized reading object that chart-shell.js
  // documents: { date, degree, charge, contaminationCount, editorial, songs[] }.
  // ============================================================

  // Long-form date, e.g. "Monday, June 9, 2026". Parsed at local midnight to
  // avoid a UTC off-by-one on the date-only string.
  function formatLongDate(dateStr) {
    if (!dateStr) return '';
    var d = new Date(String(dateStr) + 'T00:00:00');
    if (isNaN(d.getTime())) return String(dateStr);
    return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  }

  // Date split into two lines: day-of-week, then "Month D, YYYY".
  function formatDateParts(dateStr) {
    if (!dateStr) return { dow: '', mdy: '' };
    var d = new Date(String(dateStr) + 'T00:00:00');
    if (isNaN(d.getTime())) return { dow: String(dateStr), mdy: '' };
    return {
      dow: d.toLocaleDateString('en-US', { weekday: 'long' }),
      mdy: d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }),
    };
  }

  // Signed compass score from a 0..180 degree (0deg = +100, 90deg = 0,
  // 180deg = -100). Mirrors chart-shell.degreeToScore + charge_calc.py.
  function degreeToScore(degree) {
    if (degree == null) return 0;
    return Math.round((90 - degree) * 100 / 90);
  }

  // Trim a string to fit maxWidth at the current ctx.font, adding an ellipsis.
  function ellipsize(ctx, text, maxWidth) {
    var s = String(text == null ? '' : text);
    if (ctx.measureText(s).width <= maxWidth) return s;
    var ell = '…';
    while (s.length > 1 && ctx.measureText(s + ell).width > maxWidth) {
      s = s.slice(0, -1);
    }
    return s.replace(/\s+$/, '') + ell;
  }

  function pickReading(reading) {
    var r = reading || {};
    var chargeKey = (r.charge && TIER_HEX[r.charge]) ? r.charge : 'green';
    var hex = TIER_HEX[chargeKey];
    var label = TIER_LABELS[chargeKey] || 'Decent';
    var score = degreeToScore(r.degree);
    var scoreStr = (score > 0 ? '+' : '') + score;
    var songs = Array.isArray(r.songs) ? r.songs.slice() : [];
    songs.sort(function (a, b) { return (a.position || 0) - (b.position || 0); });
    var songCount = songs.length;
    var contam = r.contaminationCount || 0;
    var metaStr = songCount + ' song' + (songCount === 1 ? '' : 's') +
      '  ·  ' + contam + ' contaminated';
    return {
      hex: hex, label: label, score: score, scoreStr: scoreStr,
      dateParts: formatDateParts(r.date),
      editorial: (r.editorial && String(r.editorial).trim()) ? String(r.editorial).trim() : '',
      metaStr: metaStr, songs: songs, songCount: songCount,
      // Chart kicker (top-left label). Defaults to DAILY LISTENS so the daily
      // reading card is unchanged; the broadcaster passes DAILY DOWNLOADS /
      // SHAZAM / YOUTUBE for the other daily-chart cards in the carousel.
      kicker: (r.kicker && String(r.kicker).trim()) ? String(r.kicker).trim() : 'DAILY LISTENS',
    };
  }

  // Draw the shared CRT card chrome (background, tier bloom, scanlines,
  // vignette, phosphor border). `glowY` centers the tier bloom for the layout.
  function drawChrome(ctx, hex, glowY, H) {
    H = H || POST_H;
    var bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, '#0e0e1a');
    bg.addColorStop(0.55, '#0a0a14');
    bg.addColorStop(1, '#060609');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, SIZE, H);

    var rg = ctx.createRadialGradient(SIZE / 2, glowY, 40, SIZE / 2, glowY, SIZE * 0.62);
    rg.addColorStop(0, hexToRgba(hex, 0.18));
    rg.addColorStop(1, hexToRgba(hex, 0));
    ctx.fillStyle = rg;
    ctx.fillRect(0, 0, SIZE, H);

    ctx.fillStyle = 'rgba(0,0,0,0.05)';
    for (var sy = 0; sy < H; sy += 4) ctx.fillRect(0, sy, SIZE, 2);

    var vg = ctx.createRadialGradient(SIZE / 2, H / 2, SIZE * 0.34, SIZE / 2, H / 2, SIZE * 0.78);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.45)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, SIZE, H);

    // Flush to the image edge -- the border IS the edge (no dark margin around it).
    var blw = 7, bx = Math.round(blw / 2), bw = SIZE - blw, bh = H - blw;
    ctx.save();
    ctx.beginPath();
    ctx.rect(bx, bx, bw, bh);
    ctx.clip();
    ctx.shadowColor = hex;
    ctx.shadowBlur = 66;
    ctx.strokeStyle = hex;
    ctx.lineWidth = blw;
    ctx.strokeRect(bx, bx, bw, bh);
    ctx.strokeRect(bx, bx, bw, bh);
    ctx.strokeRect(bx, bx, bw, bh);
    ctx.restore();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = hex;
    ctx.lineWidth = blw;
    ctx.strokeRect(bx, bx, bw, bh);
  }

  // Draw the bottom brand block (compass mark + "THE RISING COMPASS" + url).
  function drawCompassBrand(ctx, compassFlat, leftX, H) {
    H = H || POST_H;
    var fy = H - 100 - 44;
    var markSize = 32;
    if (compassFlat) ctx.drawImage(compassFlat, leftX, fy - markSize / 2, markSize, markSize);
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#c8c8d8';
    ctx.font = '700 22px "JetBrains Mono"';
    setLS(ctx, 2);
    ctx.fillText('THE RISING COMPASS', leftX + markSize + 14, fy + 1);
    setLS(ctx, 0);
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#6a6a82';
    ctx.font = '400 20px "JetBrains Mono"';
    ctx.fillText('risingcompass.net', leftX, H - 100);
  }

  async function renderReading(reading, canvas, opts) {
    await ensureFonts();
    var v = pickReading(reading);
    var compassFlat = await loadCompass(0);

    var H = cardHeight(opts);
    canvas.width = SIZE;
    canvas.height = H;
    var ctx = canvas.getContext('2d');
    var P = 100;
    var PX = 110;
    var leftX = PX;
    var contentW = SIZE - PX * 2;

    drawChrome(ctx, v.hex, H * 0.40, H);

    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';

    // ----- Kicker (2.3x), full width at the top. Top-right corner stays empty
    // so Instagram's carousel icon has nothing to collide with. -----
    var kickSize = 60; // ~2.3x the prior 26px chart kicker
    ctx.fillStyle = hexToRgba(v.hex, 0.92);
    ctx.font = '700 ' + kickSize + 'px "JetBrains Mono"';
    setLS(ctx, 3);
    var kickBaseline = P + kickSize - 8;
    ctx.fillText(ellipsize(ctx, v.kicker, contentW), leftX, kickBaseline);
    setLS(ctx, 0);

    // ----- Charge: to the RIGHT of the date, below the kicker (clears the IG
    // icon, lets the body below breathe). The dominant element. The row is
    // nudged down a touch from the kicker. -----
    var charge = drawBadge(ctx, v.hex, v.label, v.scoreStr, SIZE - PX, kickBaseline + 47, {
      scoreSize: 116, labelSize: 36, padX: 44, padTop: 22, gap: -6, padBottom: 22,
      minW: 280,
    });

    // ----- Date (left of the charge), vertically centered against the charge
    // box: day-of-week, then "Month D, YYYY". -----
    var dateColW = charge.left - leftX - 40;
    var dateSize = 56, dateLH = Math.round(dateSize * 1.18);
    ctx.fillStyle = '#f4f4fa';
    ctx.font = '700 ' + dateSize + 'px "Inter"';
    var dowY = Math.round(charge.top + charge.height / 2 - (dateLH - dateSize * 0.72) / 2);
    ctx.fillText(ellipsize(ctx, v.dateParts.dow, dateColW), leftX, dowY);
    ctx.fillText(ellipsize(ctx, v.dateParts.mdy, dateColW), leftX, dowY + dateLH);
    var leftBottom = dowY + dateLH;

    // ----- Editorial: the day's statement, full width. No songs/contaminated
    // meta line -- the summary takes that space and gets more lines, since it
    // can run long. -----
    var y = Math.max(leftBottom, charge.bottom) + 56;
    if (v.editorial) {
      var eFit = fitText(ctx, v.editorial, '"Inter"', '600', contentW, 7, 38, 26);
      y += eFit.size;
      ctx.fillStyle = '#e8e8f0';
      ctx.font = '600 ' + eFit.size + 'px "Inter"';
      for (var ei = 0; ei < eFit.lines.length; ei++) {
        ctx.fillText(eFit.lines[ei], leftX, y + ei * eFit.lineHeight);
      }
      y += (eFit.lines.length - 1) * eFit.lineHeight;
    }

    // ----- Top-5 song list (text groups 1.5x, with row margin) -----
    var rows = v.songs.slice(0, 5);
    // The song list sits between the editorial and the centered footer band.
    var brandTop = H - 230;
    var listTop = y + 56;
    var rowH = 100; // title/artist group + bottom margin
    if (listTop + rows.length * rowH > brandTop) {
      listTop = Math.max(y + 32, brandTop - rows.length * rowH);
    }
    if (rows.length && listTop + rows.length * rowH > brandTop) {
      rowH = Math.max(82, Math.floor((brandTop - listTop) / rows.length));
    }
    for (var ri = 0; ri < rows.length; ri++) {
      var s = rows[ri];
      var ry = listTop + ri * rowH;
      var sHex = TIER_HEX[s.rubric_color] || '#6a6a82';
      var dim = (s.instrumental || s.preorder);
      // position
      ctx.fillStyle = '#9a9ab0';
      ctx.font = '700 34px "JetBrains Mono"';
      ctx.textAlign = 'left';
      var posStr = String(s.position == null ? ri + 1 : s.position) + (s.position_letter || '');
      ctx.fillText(posStr, leftX, ry + 40);
      // tier dot
      ctx.beginPath();
      ctx.arc(leftX + 66, ry + 30, 11, 0, Math.PI * 2);
      ctx.fillStyle = dim ? '#6a6a82' : sHex;
      ctx.fill();
      // charge (right)
      var chargeStr = '';
      if (s.preorder) chargeStr = 'Pre';
      else if (s.instrumental) chargeStr = 'Instr';
      else if (s.charge_value != null) chargeStr = (s.charge_value > 0 ? '+' : '') + s.charge_value;
      ctx.font = '700 32px "JetBrains Mono"';
      ctx.textAlign = 'right';
      ctx.fillStyle = dim ? '#6a6a82' : sHex;
      ctx.fillText(chargeStr, SIZE - PX, ry + 40);
      var chargeW = chargeStr ? ctx.measureText(chargeStr).width + 30 : 0;
      // title + artist, single line ellipsized to the remaining width
      ctx.textAlign = 'left';
      var textX = leftX + 96;
      var textW = (SIZE - PX) - chargeW - textX;
      ctx.font = '600 41px "Inter"';
      ctx.fillStyle = '#f4f4fa';
      ctx.fillText(ellipsize(ctx, s.title || 'Untitled', textW), textX, ry + 38);
      ctx.font = '400 31px "Inter"';
      ctx.fillStyle = '#9a9ab0';
      ctx.fillText(ellipsize(ctx, s.artist || '', textW), textX, ry + 74);
    }
    var listBottom = listTop + rows.length * rowH;

    // ----- Footer (centered): brand + url, enlarged, nudged up. (OG-image
    // footer layout is a later pass.) -----
    var markSize = 42;
    ctx.font = '700 32px "JetBrains Mono"';
    setLS(ctx, 2);
    var wm = 'THE RISING COMPASS';
    var wmW = ctx.measureText(wm).width;
    var totalW = markSize + 16 + wmW;
    var startX = (SIZE - totalW) / 2;
    var fy = H - P - 78;
    if (compassFlat) ctx.drawImage(compassFlat, startX, fy - markSize / 2, markSize, markSize);
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#c8c8d8';
    ctx.fillText(wm, startX + markSize + 16, fy + 1);
    setLS(ctx, 0);
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#6a6a82';
    ctx.font = '400 26px "JetBrains Mono"';
    ctx.fillText('risingcompass.net', SIZE / 2, fy + 48);
    ctx.textAlign = 'left';

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
  async function shareOrDownload(canvas, data, forceDownload, opts) {
    var brand = (opts && opts.brand) || 'lyrical-charger';
    var isCompass = brand === 'compass';
    var v = pick(data);
    var blob = await canvasToBlob(canvas);
    var fname = (isCompass ? 'rc-charge-card-' : 'rc-lc-charge-card-') +
      slugify(data.title || v.label) + '.png';
    var text = (data.title ? '"' + data.title + '" ' : '') + 'charged ' + v.chargeStr +
      ' (' + (data.tier_label || v.label) + ')' +
      (isCompass ? ' by The Rising Compass.' : ' on the Lyrical Charger.');
    var shareUrl = isCompass
      ? 'https://risingcompass.net/'
      : 'https://risingcompass.net/lyrical-charger/';

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

  window.RCChargeCard = {
    render: render,
    renderReading: renderReading,
    shareOrDownload: shareOrDownload,
    _pick: pick,
    _pickReading: pickReading,
  };
})();
