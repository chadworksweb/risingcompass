// Tier popup — clicked from .charge-legend-seg[data-tier] buttons,
// renders the tier description into #tier-popup. Single source of truth
// for tier display copy; load on any page that shows the legend.
(function() {
  const TIER_DATA = {
    ascended: { title: 'Ascended', color: 'var(--rc-violet)', desc: 'Collective perspective. The lyrics speak from, to, or about something larger than any one person. Community, humanity, the divine. Universal love, prophetic witness, collective healing. The song’s purpose is selfless.' },
    elevated: { title: 'Elevated', color: 'var(--rc-blue)', desc: 'Self without ego. Honest internal work on the page. The narrator questions their own behavior, takes accountability, processes through something with visible movement. Growth demonstrated, not just wished for. Vulnerability without performance.' },
    decent:   { title: 'Decent', color: 'var(--rc-green)', desc: 'Catch-all. The lyrics don’t push in either direction. Pleasant, fun, romantic, sad, nostalgic — but no internal work being done and no ego being served. The song is what it is. This is the baseline for popular music, and it’s not a failure.' },
    degraded: { title: 'Degraded', color: 'var(--rc-orange)', desc: 'Ego self. The lyrics serve the narrator’s ego, promote avoidance, or steer away from growth. Surface-level status markers as aspiration. Manipulation framed as love. Wallowing without movement. Blame without accountability.' },
    corrupted:{ title: 'Corrupted', color: 'var(--rc-red)', desc: 'Ego black-hole. The lyrics actively destroy, dehumanize, or consume. Explicit objectification, substance celebration as identity, violence as entertainment, possession as desire. The song’s core stance is destruction — of self, of others, of meaning itself.' }
  };

  document.addEventListener('DOMContentLoaded', function() {
    const popup = document.getElementById('tier-popup');
    if (!popup) return;
    const dot = document.getElementById('tier-popup-dot');
    const title = document.getElementById('tier-popup-title');
    const desc = document.getElementById('tier-popup-desc');

    document.querySelectorAll('.charge-legend-seg[data-tier]').forEach(btn => {
      btn.addEventListener('click', function() {
        const t = TIER_DATA[this.dataset.tier];
        if (!t) return;
        dot.style.background = t.color;
        title.textContent = t.title;
        desc.textContent = t.desc;
        popup.classList.add('active');
      });
    });

    popup.querySelector('.tier-popup-close').addEventListener('click', () => popup.classList.remove('active'));
    popup.addEventListener('click', function(e) {
      if (e.target === popup) popup.classList.remove('active');
    });
  });
})();
